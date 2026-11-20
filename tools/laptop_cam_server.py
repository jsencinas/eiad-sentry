"""Simulate the ESP32-CAM: publish the laptop webcam as MJPEG over HTTP.

Usage:
    python tools/laptop_cam_server.py [--camera 0] [--port 8081] [--width 640 --height 480]

--camera also accepts a video file, which is played in a loop (handy to test
without a webcam).

The stream is served at http://LAPTOP_IP:PORT/stream, the same shape as the
ESP32-CAM (http://ESP32_IP:81/stream), so the detector only changes the URL.
"""

from __future__ import annotations

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BOUNDARY = "frame"


class Camera:
    """Grabs frames in a background thread and keeps the latest JPEG."""

    def __init__(self, source: str, width: int, height: int, quality: int) -> None:
        self.is_file = not source.isdigit()
        self.cap = cv2.VideoCapture(source if self.is_file else int(source))
        if not self.cap.isOpened():
            raise SystemExit(f"Could not open camera {source}")
        if not self.is_file:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # Play files at their own frame rate instead of as fast as possible.
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.is_file else 0
        self.frame_delay = 1.0 / fps if fps and fps > 0 else 0.0
        self.quality = quality
        self.jpeg: bytes | None = None
        self.seq = 0
        self.cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            ok, frame = self.cap.read()
            if not ok:
                if self.is_file:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the video
                time.sleep(0.05)
                continue
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", default="0", help="webcam index or video file (default: 0)")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8081, help="port (default: 8081)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality 1-100 (default: 80)")
    args = parser.parse_args()

    camera = Camera(args.camera, args.width, args.height, args.quality)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(camera))
    server.daemon_threads = True
    print(f"Streaming camera {args.camera} at http://{args.host}:{args.port}/stream (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
