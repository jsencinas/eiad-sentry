"""Command line entry point: stream -> inference -> tracking -> smoothing -> alerts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sentry.association import CenterInBoxRule, assign_ppe, owners
from sentry.classes import ClassNameError, class_ids
from sentry.cooldown import Cooldown
from sentry.smoothing import Smoother

DEMO_MODEL = "yolov8n.pt"
DEMO_DEFAULT_ITEM = "backpack"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ppe_detector.py",
        description="Detect people and check their PPE on a video stream.",
    )
    p.add_argument("--source", required=True, help="stream URL, webcam index (0) or video file")
    p.add_argument(
        "--rotate",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="turn every frame clockwise by this many degrees, for a camera mounted sideways "
        "(e.g. 270 if the image arrives turned 90 degrees to the right)",
    )
    p.add_argument("--demo", action="store_true", help="prototype mode with COCO yolov8n.pt and a substitute object")
    p.add_argument(
        "--require",
        default=DEMO_DEFAULT_ITEM,
        help="demo only: COCO object(s) standing in for PPE, comma separated (default: backpack)",
    )
    p.add_argument("--model", help=f"YOLO weights (demo default: {DEMO_MODEL})")
    p.add_argument("--conf", type=float, default=0.4, help="minimum confidence (default: 0.4)")
    p.add_argument("--imgsz", type=int, default=640, help="inference image size (default: 640)")
    p.add_argument("--save-dir", type=Path, help="save the annotated frame of each alert here")
    p.add_argument("--cooldown", type=float, default=10.0, help="seconds between alerts for the same person (default: 10)")
    p.add_argument("--window", type=int, default=15, help="frames in the smoothing window (default: 15)")
    p.add_argument("--min-samples", type=int, default=5, help="samples needed before deciding (default: 5)")
    p.add_argument("--stats-interval", type=float, default=5.0, help="seconds between stats lines (default: 5)")
    p.add_argument("--show", action="store_true", help="show a window (needs a desktop, e.g. on the laptop)")
    p.add_argument("--web", action="store_true", help="serve the annotated stream in a web page")
    p.add_argument("--web-host", help="address for the web viewer (default: Tailscale IP, else 127.0.0.1)")
    p.add_argument("--web-port", type=int, default=8082, help="port for the web viewer (default: 8082)")
    p.add_argument(
        "--credential",
        action="store_true",
        help="identify each person by the matrícula on their Tec credential (OCR) before "
        "checking their PPE; with --db, record their entry and exit",
    )
    p.add_argument(
        "--db",
        metavar="DSN",
        help="write alerts and the live state to the lab-monitor PostgreSQL database, "
        "e.g. postgresql:///lab_monitor",
    )
    p.add_argument(
        "--db-map",
        metavar="ITEM=glasses|coat",
        help='which database item each required item stands for, e.g. "cell phone=glasses" '
        "(items called glasses or coat map to themselves)",
    )
    return p


def setup_demo(args):
    """Return (detector, required items, association rule)."""
    from sentry.inference import YoloDetector, load_model

    required = [r.strip() for r in args.require.split(",") if r.strip()]
    if not required:
        raise SystemExit("--require needs at least one object name")
    model = load_model(args.model or DEMO_MODEL)
    try:
        ids = class_ids(model.names, {"person": "person", **{r: r for r in required}})
    except ClassNameError as e:
        raise SystemExit(f"Error: {e}") from None
    ppe_classes = {ids[r]: r for r in required}
    detector = YoloDetector(model, ids["person"], model, ppe_classes, conf=args.conf, imgsz=args.imgsz)
    return detector, required, CenterInBoxRule()


def status_payload(results, stats_line: str, waiting=(), names=None) -> dict:
    names = names or {}
    return {
        "stats": stats_line,
        "people": [
            {
                "id": tid,
                "verdict": "SIN_CREDENCIAL" if tid in waiting else r.verdict.value,
                "missing": [] if tid in waiting else list(r.missing),
                "matricula": names.get(tid),
            }
            for tid, r in sorted(results.items())
        ],
    }


def update_identities(identities, credential_reader, smoother, results, found, frame, db) -> list[int]:
    """Feed OCR reads, report arrivals and departures; return who still has to show a credential."""
    from sentry.credentials import assign_reads

    for reads, persons in credential_reader.results():
        for track_id, matricula in identities.add_reads(assign_reads(reads, persons)).items():
            print(f"[credential] #{track_id} identified as {matricula}")
            if db is not None:
                db.identified(track_id, matricula)
    for track_id, matricula in identities.forget_missing(smoother.tracked_ids).items():
        print(f"[credential] #{track_id} ({matricula}) left")
        if db is not None:
            db.departed(track_id)
    waiting = identities.needs_credential(results)
    if waiting and credential_reader.due():
        credential_reader.submit(frame.copy(), found.persons)
    return waiting


def run(args) -> None:
    import cv2

    from sentry.output import AlertDispatcher, StatsPrinter, annotate, on_violation
    from sentry.stream import LatestFrameReader

    if args.demo:
        detector, required, rule = setup_demo(args)
    else:
        raise SystemExit("Only --demo mode is available for now.")

    db = None
    if args.db:
        from sentry.database import DatabaseWriter, parse_db_map

        try:
            item_map = parse_db_map(args.db_map, required)
        except ValueError as e:
            raise SystemExit(f"Error: {e}") from None
        db = DatabaseWriter(args.db, item_map, credentials=args.credential).start()
        stands_for = ", ".join(f"{item} as {'+'.join(targets)}" for item, targets in item_map.items())
        print(f"[db] Writing to {args.db} ({stands_for})")
    elif args.db_map:
        raise SystemExit("--db-map needs --db")

    def violation(track_id, missing, frame, save_dir):
        photo = on_violation(track_id, missing, frame, save_dir)
        if db is not None:
            db.incident(track_id, missing, photo.resolve() if photo else None)
        return photo

    identities = credential_reader = None
    if args.credential:
        from sentry.credentials import IdentityTracker
        from sentry.ocr import CredentialReader

        identities = IdentityTracker()
        credential_reader = CredentialReader().start()
        print("[credential] Each person must show their credential before their PPE is checked")

    smoother = Smoother(required, window=args.window, min_samples=args.min_samples)
    alerts = AlertDispatcher(Cooldown(args.cooldown), args.save_dir, hook=violation)
    stats = StatsPrinter(args.stats_interval)

    hub = viewer = None
    if args.web:
        from sentry.web import FrameHub, WebViewer, resolve_host

        hub = FrameHub()
        try:
            viewer = WebViewer(hub, resolve_host(args.web_host), args.web_port).start()
        except OSError as e:
            raise SystemExit(f"Could not start the web viewer: {e}") from None

    print(f"[sentry] Mode: {'demo' if args.demo else 'real'} | required: {', '.join(required)}")
    reader = LatestFrameReader(args.source).start()
    rotation = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }.get(args.rotate)
    seq = 0
    try:
        while True:
            seq, frame = reader.read(seq)
            if frame is None:
                if reader.finished:
                    break
                continue
            if rotation is not None:
                frame = cv2.rotate(frame, rotation)  # ~1 ms; only analyzed frames are turned

            found = detector(frame)
            observations = assign_ppe(found.persons, found.ppe, required, rule, frame.shape[0])
            results = smoother.update(observations)
            stats.update(found.inference_ms, results)

            waiting: list[int] = []
            if identities is not None:
                waiting = update_identities(identities, credential_reader, smoother, results, found, frame, db)
            if db is not None:
                db.live(results, unidentified=waiting)

            names = identities.identities if identities is not None else {}
            annotated = annotate(
                frame, found.persons, found.ppe, results,
                owners(found.persons, found.ppe, rule),
                header=f"{found.inference_ms:.0f} ms",
                waiting=waiting, names=names,
            )
            # PPE is only judged once the person is identified.
            alerts({t: r for t, r in results.items() if t not in waiting}, annotated)
            if hub is not None:
                hub.publish(annotated, status_payload(results, stats.last_line, waiting, names))
            if args.show:
                try:
                    cv2.imshow("Sentry", annotated)
                except cv2.error:
                    print("[sentry] --show needs OpenCV with GUI support and a desktop; disabling it")
                    args.show = False
                    continue
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print("[sentry] Stopping")
        reader.stop()
        if credential_reader is not None:
            credential_reader.stop()
        if db is not None:
            db.close()
        if viewer is not None:
            viewer.stop()
        if args.show:
            cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not 1 <= args.min_samples <= args.window:
        sys.exit("--min-samples must be between 1 and --window")
    run(args)
