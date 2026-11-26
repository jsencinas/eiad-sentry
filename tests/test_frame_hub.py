from sentry.web import FrameHub


def make_hub():
    calls = []

    def encoder(frame):
        calls.append(frame)
        return f"jpeg:{frame}".encode()

    return FrameHub(encoder=encoder), calls


def test_no_encoding_while_nobody_watches():
    hub, calls = make_hub()
    for i in range(5):
        hub.publish(f"frame{i}")
    assert calls == []


def test_snapshot_is_the_latest_frame_not_the_first():
    hub, calls = make_hub()
    hub.publish("frame0")
    hub.publish("frame1")
    assert hub.latest() == b"jpeg:frame1"
    assert hub.latest() == b"jpeg:frame1"
    assert calls == ["frame1"]  # encoded once, then cached


def test_snapshot_before_any_frame():
    hub, _ = make_hub()
    assert hub.latest() is None


def test_watched_frames_are_encoded_and_streamed():
    hub, calls = make_hub()
    hub.client_joined()
    hub.publish("frame0")
    seq, jpeg = hub.wait_next(0, timeout=0)
    assert jpeg == b"jpeg:frame0"
    hub.publish("frame1")
    assert hub.wait_next(seq, timeout=0)[1] == b"jpeg:frame1"
    assert hub.latest() == b"jpeg:frame1"
    assert calls == ["frame0", "frame1"]  # the snapshot reused the cached JPEG
