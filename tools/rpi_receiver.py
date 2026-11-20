"""Headless check that the Raspberry Pi can read the MJPEG stream.

Usage:
    python tools/rpi_receiver.py http://LAPTOP_IP:8081/stream [--seconds 0]

Prints the resolution and the received fps every few seconds. No display
needed: every --snapshot-every frames it saves the latest one to --snapshot
(default last_frame.jpg) so you can check what the Pi is receiving.
"""

from __future__ import annotations

import argparse
import time

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="stream URL, e.g. http://LAPTOP_IP:8081/stream")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between reports (default: 5)")
    parser.add_argument("--snapshot", default="last_frame.jpg", help="file for the latest frame ('' to disable)")
    parser.add_argument("--snapshot-every", type=int, default=30, help="save a snapshot every N frames (default: 30)")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = run forever)")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.url)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {args.url}")

    start = last = time.monotonic()
    frames = total = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Frame lost, reconnecting...")
                cap.release()
                time.sleep(1.0)
                cap = cv2.VideoCapture(args.url)
                continue
            frames += 1
            total += 1
            if args.snapshot and total % args.snapshot_every == 0:
                cv2.imwrite(args.snapshot, frame)
            now = time.monotonic()
            if now - last >= args.interval:
                h, w = frame.shape[:2]
                print(f"{w}x{h} at {frames / (now - last):.1f} fps")
                frames, last = 0, now
            if args.seconds and now - start >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
