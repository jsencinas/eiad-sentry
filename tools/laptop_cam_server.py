"""Simulate the ESP32-CAM: publish the laptop webcam as MJPEG over HTTP.

Usage:
    python tools/laptop_cam_server.py [--camera 0] [--port 8081] [--width 640 --height 480]

--camera also accepts a device path or a video file, which is played in a loop
(handy to test without a webcam). If the webcam disconnects it is reopened; on
Linux a stable path such as /dev/v4l/by-id/usb-...-video-index0 survives a USB
reconnect that changes the index.

The stream is served at http://LAPTOP_IP:PORT/stream, the same shape as the
ESP32-CAM (http://ESP32_IP:81/stream), so the detector only changes the URL.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BOUNDARY = "frame"


class Camera:
    """Grabs frames in a background thread and keeps the latest JPEG."""

    def __init__(self, source: str, width: int, height: int, quality: int) -> None:
        self.source, self.width, self.height = source, width, height
        self.is_file = os.path.isfile(source)  # loop video files; /dev/video* are cameras
        self.cap = self._open()
        if self.cap is None:
            raise SystemExit(f"Could not open camera {source}")
        # Play files at their own frame rate instead of as fast as possible.
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.is_file else 0
        self.frame_delay = 1.0 / fps if fps and fps > 0 else 0.0
        self.quality = quality
        self.jpeg: bytes | None = None
        self.seq = 0
        self.cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self):
        cap = cv2.VideoCapture(int(self.source) if self.source.isdigit() else self.source)
        if not cap.isOpened():
            cap.release()
            return None
        if not self.is_file:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        return cap

    def _loop(self) -> None:
        failures = 0
        while True:
            ok, frame = self.cap.read() if self.cap is not None else (False, None)
            if not ok:
                if self.is_file and self.cap is not None:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the video
                    continue
                # A webcam that was unplugged or reset never recovers on the old handle.
                failures += 1
                if failures >= 20:
                    print("Camera lost, reopening...")
                    if self.cap is not None:
                        self.cap.release()
                    self.cap = self._open()
                    failures = 0
                    time.sleep(1.0)
                time.sleep(0.05)
                continue
            failures = 0
            if self.frame_delay:
                time.sleep(self.frame_delay)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if not ok:
                continue
            with self.cond:
                self.jpeg = buf.tobytes()
                self.seq += 1
                self.cond.notify_all()

    def wait_next(self, last_seq: int, timeout: float = 2.0) -> tuple[int, bytes | None]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq, timeout=timeout)
            return self.seq, self.jpeg


def make_handler(camera: Camera) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path != "/stream":
                self.send_response(302)
                self.send_header("Location", "/stream")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seq = -1
            try:
                while True:
                    seq, jpeg = camera.wait_next(seq)
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt: str, *args) -> None:
            print(f"[{self.client_address[0]}] {fmt % args}")

    return Handler


def reachable_ips() -> list[str]:
    """Addresses other machines can use to reach this laptop (LAN and Tailscale)."""
    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # no packet is sent; just picks the outgoing interface
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3).stdout
        ips += [ip for ip in out.split() if ip not in ips]
    except (OSError, subprocess.SubprocessError):
        pass
    return ips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", default="0", help="webcam index or video file (default: 0)")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8081, help="port (default: 8081)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=70, help="JPEG quality 1-100, lower it on a slow network (default: 70)")
    args = parser.parse_args()

    camera = Camera(args.camera, args.width, args.height, args.quality)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(camera))
    server.daemon_threads = True
    print(f"Streaming camera {args.camera} (Ctrl+C to stop)")
    if args.host == "0.0.0.0":
        # 0.0.0.0 means "listen on every interface"; it is not an address to connect to.
        print(f"  this laptop:   http://localhost:{args.port}/stream")
        for ip in reachable_ips():
            print(f"  other devices: http://{ip}:{args.port}/stream")
    else:
        print(f"  http://{args.host}:{args.port}/stream")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
