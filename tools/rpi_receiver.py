"""Headless check that the Raspberry Pi can read the MJPEG stream.

Usage:
    python tools/rpi_receiver.py http://LAPTOP_IP:8081/stream [--seconds 0]

Prints the resolution and the received fps every few seconds. No display needed.
"""

from __future__ import annotations

import argparse
import time

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="stream URL, e.g. http://LAPTOP_IP:8081/stream")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between reports (default: 5)")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = run forever)")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.url)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {args.url}")

    start = last = time.monotonic()
    frames = 0
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
