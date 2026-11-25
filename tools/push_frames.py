"""Simulate the ESP32-CAM push firmware: POST webcam frames to the Pi's ingest server.

Usage:
    SENTRY_INGEST_TOKEN=... python tools/push_frames.py https://PI_NAME.TAILNET.ts.net/frame

Sends one JPEG per request over a single keep-alive connection, exactly like
firmware/esp32cam_push, so the whole path (Funnel -> frame_ingest.py ->
ppe_detector.py) can be tested from the laptop before flashing the camera.
--camera takes a webcam index, a video file (looped) or a stream URL.
"""

from __future__ import annotations

import argparse
import http.client
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import cv2

TOKEN_ENV = "SENTRY_INGEST_TOKEN"


def open_source(source: str):
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {source}")
    return cap


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="upload URL, e.g. https://PI_NAME.TAILNET.ts.net/frame")
    p.add_argument("--camera", default="0", help="webcam index, video file or stream URL (default: 0)")
    p.add_argument("--token-file", help=f"file holding the upload token (default: ${TOKEN_ENV})")
    p.add_argument("--fps", type=float, default=10.0, help="maximum frames per second (default: 10)")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--quality", type=int, default=70, help="JPEG quality 1-100 (default: 70)")
    p.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = run forever)")
    args = p.parse_args()

    token = Path(args.token_file).expanduser().read_text().strip() if args.token_file else os.environ.get(TOKEN_ENV, "")
    if not token:
        raise SystemExit(f"Pass --token-file or set {TOKEN_ENV}")
    url = urlsplit(args.url)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise SystemExit("The URL must start with http:// or https://")
    conn_class = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    path = url.path or "/frame"

    cap = open_source(args.camera)
    is_file = not args.camera.isdigit() and "://" not in args.camera
    conn = None
    sent = 0
    start = last_report = time.monotonic()
    min_gap = 1.0 / args.fps if args.fps > 0 else 0.0
    try:
        while not args.seconds or time.monotonic() - start < args.seconds:
            t0 = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                if is_file:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                print("Camera read failed, retrying...")
                time.sleep(1.0)
                continue
            if frame.shape[1] != args.width or frame.shape[0] != args.height:
                frame = cv2.resize(frame, (args.width, args.height))
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            if not ok:
                continue

            try:
                if conn is None:
                    conn = conn_class(url.hostname, url.port, timeout=10)
                conn.request(
                    "POST", path, body=buf.tobytes(),
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
                )
                resp = conn.getresponse()
                resp.read()
            except (OSError, http.client.HTTPException) as e:
                print(f"Upload failed ({e}), reconnecting...")
                if conn is not None:
                    conn.close()
                conn = None
                time.sleep(1.0)
                continue
            if resp.status == 401:
                sys.exit("The server rejected the token (401)")
            if resp.status != 204:
                print(f"Server answered {resp.status}")
            else:
                sent += 1

            now = time.monotonic()
            if now - last_report >= 5:
                print(f"{sent / (now - last_report):.1f} fps sent, {len(buf) // 1000} KB per frame")
                sent, last_report = 0, now
            wait = min_gap - (now - t0)
            if wait > 0:
                time.sleep(wait)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
