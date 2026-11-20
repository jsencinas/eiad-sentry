"""Live web viewer: the annotated stream as MJPEG, viewable in any browser.

Routes:
    /              HTML page with the live video and the current verdicts
    /stream        multipart MJPEG of the annotated frames
    /snapshot.jpg  latest annotated frame
    /status.json   latest verdicts and stats

The video shows students, so by default the server only listens on the Pi's
Tailscale address (reachable only from your own Tailscale devices), never on
every interface unless asked explicitly.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = "frame"
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def tailscale_ipv4() -> str | None:
    """This machine's Tailscale IPv4 address, or None if Tailscale is not up."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.split():
        try:
            if ipaddress.ip_address(line) in TAILSCALE_NET:
                return line
        except ValueError:
            continue
    return None


def resolve_host(requested: str | None) -> str:
    """Address to bind: the one requested, else Tailscale, else localhost only."""
    if requested:
        return requested
    ip = tailscale_ipv4()
    if ip:
        return ip
    print("[web] Tailscale address not found: listening on 127.0.0.1 only (use --web-host to change)")
    return "127.0.0.1"


class FrameHub:
    """Latest annotated frame shared between the detector and the HTTP clients.

    Frames are only JPEG-encoded while somebody is watching.
    """

    def __init__(self, quality: int = 75) -> None:
        self.quality = quality
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._clients = 0
        self._status: dict = {}
        self._closed = False

    @property
    def has_clients(self) -> bool:
        return self._clients > 0

    def publish(self, frame, status: dict | None = None) -> None:
        jpeg = None
        if self._clients > 0 or self._jpeg is None:
            import cv2  # lazy: keeps the host-selection logic testable without OpenCV

            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            jpeg = buf.tobytes() if ok else None
        with self._cond:
            if jpeg is not None:
                self._jpeg = jpeg
                self._seq += 1
            if status is not None:
                self._status = status
            self._cond.notify_all()

    def latest(self) -> bytes | None:
        with self._cond:
            return self._jpeg

    def status(self) -> dict:
        with self._cond:
            return dict(self._status)

    def wait_next(self, last_seq: int, timeout: float = 5.0) -> tuple[int, bytes | None]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq != last_seq or self._closed, timeout=timeout)
            return self._seq, (self._jpeg if self._seq != last_seq else None)

    def client_joined(self) -> None:
        with self._cond:
            self._clients += 1

    def client_left(self) -> None:
        with self._cond:
            self._clients -= 1

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentry live</title>
<style>
  body { margin: 0; font: 15px system-ui, sans-serif; background: #111; color: #eee; }
  main { max-width: 960px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 18px; margin: 0 0 12px; }
  img { width: 100%; height: auto; background: #000; border-radius: 6px; display: block; }
  #stats { color: #aaa; margin: 8px 0; }
  ul { list-style: none; padding: 0; margin: 0; }
  li { padding: 6px 10px; margin: 4px 0; border-radius: 4px; background: #222; }
  .OK { border-left: 4px solid #3c3; } .MISSING { border-left: 4px solid #e33; }
  .UNVERIFIABLE { border-left: 4px solid #eb0; } .PENDING { border-left: 4px solid #888; }
</style>
</head>
<body>
<main>
  <h1>Sentry: PPE detection (live)</h1>
  <img src="/stream" alt="Live annotated stream">
  <div id="stats"></div>
  <ul id="people"></ul>
</main>
<script>
async function refresh() {
  try {
    const s = await (await fetch('/status.json', {cache: 'no-store'})).json();
    document.getElementById('stats').textContent = s.stats || '';
    const ul = document.getElementById('people');
    ul.replaceChildren(...(s.people || []).map(p => {
      const li = document.createElement('li');
      li.className = p.verdict;
      li.textContent = '#' + p.id + '  ' + p.verdict + (p.missing.length ? ': ' + p.missing.join(', ') : '');
      return li;
    }));
  } catch (e) { /* detector restarting */ }
}
setInterval(refresh, 1000); refresh();
</script>
</body>
</html>
"""


def make_handler(hub: FrameHub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/status.json":
                self._send(200, "application/json", json.dumps(hub.status()).encode())
            elif path == "/snapshot.jpg":
                jpeg = hub.latest()
                if jpeg is None:
                    self._send(503, "text/plain", b"No frame yet\n")
                else:
                    self._send(200, "image/jpeg", jpeg)
            elif path == "/stream":
                self._stream()
            else:
                self._send(404, "text/plain", b"Not found\n")

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            hub.client_joined()
            seq = -1
            try:
                while not hub.closed:
                    seq, jpeg = hub.wait_next(seq)
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
            finally:
                hub.client_left()

        def log_message(self, fmt: str, *args) -> None:
            pass  # keep the console for detector output

    return Handler


class WebViewer:
    def __init__(self, hub: FrameHub, host: str, port: int) -> None:
        self.hub = hub
        self.server = ThreadingHTTPServer((host, port), make_handler(hub))
        self.server.daemon_threads = True
        self._thread = threading.Thread(target=self.server.serve_forever, name="web-viewer", daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> "WebViewer":
        self._thread.start()
        print(f"[web] Live viewer at {self.url}")
        return self

    def stop(self) -> None:
        self.hub.close()
        self.server.shutdown()
        self.server.server_close()
