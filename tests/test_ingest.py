import http.client

import pytest

from sentry.ingest import IngestServer, is_jpeg, token_ok

TOKEN = "test-token-0123456789"
JPEG = b"\xff\xd8" + b"\x00" * 200 + b"\xff\xd9"


@pytest.fixture
def server():
    s = IngestServer(TOKEN, ("127.0.0.1", 0), ("127.0.0.1", 0), max_bytes=10_000).start()
    yield s
    s.stop()


def post(conn, body, token=TOKEN, path="/frame", headers=None):
    h = {"Content-Type": "image/jpeg"}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    conn.request("POST", path, body=body, headers=h)
    resp = conn.getresponse()
    resp.read()
    return resp.status


def upload_conn(server):
    return http.client.HTTPConnection("127.0.0.1", server.upload_port, timeout=5)


def test_token_check():
    assert token_ok(f"Bearer {TOKEN}", TOKEN)
    assert not token_ok("Bearer wrong", TOKEN)
    assert not token_ok(TOKEN, TOKEN)
    assert not token_ok(None, TOKEN)


def test_is_jpeg():
    assert is_jpeg(JPEG)
    assert not is_jpeg(b"GIF89a" + b"\x00" * 200)
    assert not is_jpeg(b"\xff\xd8")


def test_valid_frames_on_one_connection(server):
    conn = upload_conn(server)
    assert post(conn, JPEG) == 204
    assert post(conn, JPEG) == 204  # keep-alive: same connection
    assert server.store.received == 2


@pytest.mark.parametrize("token", [None, "wrong-token-0123456789"])
def test_bad_token_rejected(server, token):
    assert post(upload_conn(server), JPEG, token=token) == 401
    assert server.store.received == 0


def test_wrong_path(server):
    assert post(upload_conn(server), JPEG, path="/stream") == 404


def test_not_a_jpeg(server):
    assert post(upload_conn(server), b"hello" * 50) == 400
    assert server.store.received == 0


def test_too_large(server):
    assert post(upload_conn(server), b"\xff\xd8" + b"\x00" * 20_000) == 413


def test_chunked_upload_refused(server):
    conn = upload_conn(server)
    conn.request(
        "POST", "/frame", body=iter([JPEG]),
        headers={"Authorization": f"Bearer {TOKEN}", "Transfer-Encoding": "chunked"},
    )
    assert conn.getresponse().status == 411


def test_upload_port_never_serves_video(server):
    post(upload_conn(server), JPEG)
    conn = upload_conn(server)
    conn.request("GET", "/stream")
    assert conn.getresponse().status == 404


def test_stream_returns_uploaded_frame(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.stream_port, timeout=5)
    conn.request("GET", "/stream")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("multipart/x-mixed-replace")
    post(upload_conn(server), JPEG)
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        head += resp.read(1)
    assert b"Content-Length: %d" % len(JPEG) in head
    assert resp.read(len(JPEG)) == JPEG
