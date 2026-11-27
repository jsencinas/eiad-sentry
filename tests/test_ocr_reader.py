from sentry.association import Box, Person
from sentry.credentials import TextRead
from sentry.ocr import CredentialReader


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def reader(**kw):
    clock = Clock()
    return CredentialReader(interval=1.0, engine_factory=lambda: None, clock=clock, log=lambda m: None, **kw), clock


CARD = TextRead("A01234567", Box(10, 10, 50, 20))
ALICE = Person(1, Box(0, 0, 100, 300))


def test_submissions_are_rate_limited():
    r, clock = reader()
    assert r.due()
    r.submit("frame", [ALICE])
    assert not r.due()            # a frame is waiting
    r.run_once(lambda f: [])
    assert not r.due()            # less than 1 s since the last submit
    clock.t = 1.0
    assert r.due()


def test_only_the_newest_frame_is_read():
    r, _ = reader()
    seen = []
    r.submit("old", [ALICE])
    r.submit("new", [ALICE])
    r.run_once(lambda f: seen.append(f) or [CARD])
    assert seen == ["new"]
    assert not r.run_once(lambda f: [])   # nothing left


def test_results_come_with_the_people_of_that_frame():
    r, _ = reader()
    r.submit("frame", [ALICE])
    r.run_once(lambda f: [CARD])
    assert r.results() == [([CARD], [ALICE])]
    assert r.results() == []


def test_an_ocr_error_does_not_stop_the_reader():
    r, _ = reader()

    def broken(frame):
        raise RuntimeError("bad image")

    r.submit("frame", [ALICE])
    assert r.run_once(broken)
    assert r.results() == [([], [ALICE])]
