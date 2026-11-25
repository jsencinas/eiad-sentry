"""Frame ingest: receive JPEG frames pushed by the ESP32-CAM and re-serve them as MJPEG.

The camera and the Pi are on different networks, and the ESP32 cannot run
Tailscale. So the camera pushes frames over HTTPS to a public Tailscale Funnel
URL of the Pi, and this server hands them to ppe_detector.py as a normal
local MJPEG stream.

Two listeners, on purpose:

    upload  127.0.0.1:8083   POST /frame, header "Authorization: Bearer TOKEN",
                             body = one JPEG. The only port exposed by Funnel.
    stream  127.0.0.1:8084   GET /stream, MJPEG for ppe_detector.py. Never exposed.

Funnel forwards every path of the public URL to the upload port, so the video
is never served there: the upload port only accepts frames, it never returns
any.
"""

from __future__ import annotations

import argparse
import hmac
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BOUNDARY = "frame"
JPEG_SOI = b"\xff\xd8"
MIN_TOKEN_LENGTH = 16
TOKEN_ENV = "SENTRY_INGEST_TOKEN"


def token_ok(header: str | None, token: str) -> bool:
    """True if an Authorization header carries the expected bearer token."""
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].strip().encode(), token.encode())


def is_jpeg(data: bytes) -> bool:
    return len(data) > 64 and data.startswith(JPEG_SOI)


def load_token(token_file: str | None) -> str:
    """Token from --token-file, else from the SENTRY_INGEST_TOKEN variable."""
    if token_file:
        try:
            token = Path(token_file).expanduser().read_text().strip()
        except OSError as e:
            raise SystemExit(f"Could not read the token file: {e}") from None
    else:
        token = os.environ.get(TOKEN_ENV, "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        raise SystemExit(
            f"A token of at least {MIN_TOKEN_LENGTH} characters is required "
            f"(--token-file or {TOKEN_ENV}). Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return token


class FrameStore:
    """Latest received JPEG, shared between the upload and stream handlers."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        self.received = 0
        self.rejected = 0
        self.last_time: float | None = None
        self._closed = False

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self._jpeg = jpeg
            self._seq += 1
            self.received += 1
            self.last_time = time.monotonic()
            self._cond.notify_all()

    def reject(self) -> None:
        with self._cond:
            self.rejected += 1

    def wait_next(self, last_seq: int, timeout: float = 5.0) -> tuple[int, bytes | None]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq != last_seq or self._closed, timeout=timeout)
            return self._seq, (self._jpeg if self._seq != last_seq else None)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed


def make_upload_handler(store: FrameStore, token: str, max_bytes: int) -> type[BaseHTTPRequestHandler]:
    warn_lock = threading.Lock()
    last_warning = [0.0]

    class UploadHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: the camera reuses one TLS connection
        timeout = 30  # drop idle or stalled connections
        server_version = "sentry-ingest"
        sys_version = ""

        def _reply(self, code: int, body: bytes = b"", close: bool = False) -> None:
            if close:
                self.close_connection = True
            self.send_response(code)
            if code != 204:
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
            if close:
                self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _warn(self, reason: str) -> None:
            store.reject()
            with warn_lock:
                now = time.monotonic()
                if now - last_warning[0] < 10:
                    return
                last_warning[0] = now
            # Behind Funnel every request comes from 127.0.0.1; the real client is in this header.
            client = self.headers.get("X-Forwarded-For") or self.client_address[0]
            print(f"[ingest] Rejected upload from {client}: {reason}")

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            # Errors before the body is read close the connection, since the
            # unread body would otherwise be parsed as the next request.
            if self.path.split("?", 1)[0] != "/frame":
                return self._reply(404, b"Not found\n", close=True)
            if not token_ok(self.headers.get("Authorization"), token):
                self._warn("bad or missing token")
                return self._reply(401, b"Unauthorized\n", close=True)
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                return self._reply(411, b"Content-Length required\n", close=True)
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411, b"Content-Length required\n", close=True)
            if length <= 0:
                return self._reply(400, b"Empty body\n", close=True)
            if length > max_bytes:
                self._warn(f"frame too large ({length} bytes)")
                return self._reply(413, b"Frame too large\n", close=True)

            data = self.rfile.read(length)
            if len(data) < length:
                self.close_connection = True  # client went away mid-frame
                return
            if not is_jpeg(data):
                self._warn("body is not a JPEG")
                return self._reply(400, b"Not a JPEG\n")
            store.publish(data)
            self._reply(204)

        def do_GET(self) -> None:  # noqa: N802
            self._reply(404, b"Not found\n", close=True)

        def log_message(self, fmt: str, *args) -> None:
            pass

    return UploadHandler


def make_stream_handler(store: FrameStore) -> type[BaseHTTPRequestHandler]:
    class StreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/stream")
                self.end_headers()
                return
            if path != "/stream":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = -1
            try:
                while not store.closed:
                    seq, jpeg = store.wait_next(seq)
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
            pass

    return StreamHandler


class IngestServer:
    def __init__(
        self,
        token: str,
        upload_addr: tuple[str, int] = ("127.0.0.1", 8083),
        stream_addr: tuple[str, int] = ("127.0.0.1", 8084),
        max_bytes: int = 1_000_000,
    ) -> None:
        self.store = FrameStore()
        self.upload = ThreadingHTTPServer(upload_addr, make_upload_handler(self.store, token, max_bytes))
        self.stream = ThreadingHTTPServer(stream_addr, make_stream_handler(self.store))
        self._threads = []
        for name, server in (("upload", self.upload), ("stream", self.stream)):
            server.daemon_threads = True
            self._threads.append(threading.Thread(target=server.serve_forever, args=(0.1,), name=f"ingest-{name}", daemon=True))

    @property
    def upload_port(self) -> int:
        return self.upload.server_address[1]

    @property
    def stream_port(self) -> int:
        return self.stream.server_address[1]

    def start(self) -> "IngestServer":
        for t in self._threads:
            t.start()
        return self

    def stop(self) -> None:
        self.store.close()
        for server in (self.upload, self.stream):
            server.shutdown()
            server.server_close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="frame_ingest.py",
        description="Receive JPEG frames pushed by the camera and re-serve them as local MJPEG.",
    )
    p.add_argument("--token-file", help=f"file holding the upload token (default: ${TOKEN_ENV})")
    p.add_argument("--upload-host", default="127.0.0.1", help="upload listener; Funnel proxies to it (default: 127.0.0.1)")
    p.add_argument("--upload-port", type=int, default=8083, help="upload port (default: 8083)")
    p.add_argument("--stream-host", default="127.0.0.1", help="MJPEG listener for the detector (default: 127.0.0.1)")
    p.add_argument("--stream-port", type=int, default=8084, help="MJPEG port (default: 8084)")
    p.add_argument("--max-frame-kb", type=int, default=1000, help="largest accepted frame in KB (default: 1000)")
    p.add_argument("--stats-interval", type=float, default=5.0, help="seconds between stats lines (default: 5)")
    args = p.parse_args(argv)

    token = load_token(args.token_file)
    try:
        server = IngestServer(
            token,
            (args.upload_host, args.upload_port),
            (args.stream_host, args.stream_port),
            args.max_frame_kb * 1000,
        ).start()
    except OSError as e:
        raise SystemExit(f"Could not start the ingest server: {e}") from None

    print(f"[ingest] Uploads: POST http://{args.upload_host}:{args.upload_port}/frame (expose with: tailscale funnel)")
    print(f"[ingest] Detector source: http://{args.stream_host}:{args.stream_port}/stream")
    store = server.store
    last_count = 0
    try:
        while True:
            time.sleep(args.stats_interval)
            count, last = store.received, store.last_time
            fps = (count - last_count) / args.stats_interval
            last_count = count
            if last is None:
                print("[ingest] Waiting for the camera...")
            elif fps == 0:
                print(f"[ingest] No frames for {time.monotonic() - last:.0f} s")
            else:
                print(f"[ingest] {fps:.1f} fps received | rejected: {store.rejected}")
    except KeyboardInterrupt:
        pass
    finally:
        print("[ingest] Stopping")
        server.stop()
