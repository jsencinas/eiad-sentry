import pytest

from sentry.association import Status
from sentry.smoothing import Decision, Smoother, Verdict, decide

P, A, U = Status.PRESENT, Status.ABSENT, Status.UNVERIFIABLE


def feed(smoother, track_id, seq, item="backpack"):
    result = None
    for status in seq:
        result = smoother.update({track_id: {item: status}})[track_id]
    return result


def test_pending_until_min_samples():
    s = Smoother(["backpack"], window=15, min_samples=5)
    assert feed(s, 1, [A] * 4).verdict is Verdict.PENDING
    assert feed(s, 1, [A]).verdict is Verdict.MISSING


def test_isolated_bad_frame_does_not_alert():
    s = Smoother(["backpack"])
    feed(s, 1, [P] * 10)
    result = feed(s, 1, [A])
    assert result.verdict is Verdict.OK
    assert feed(s, 1, [P] * 3).verdict is Verdict.OK


def test_sustained_absence_alerts():
    s = Smoother(["backpack"], window=15)
    feed(s, 1, [P] * 15)
    assert feed(s, 1, [A] * 7).verdict is Verdict.OK  # 8 P vs 7 A in the window
    result = feed(s, 1, [A])
    assert result.verdict is Verdict.MISSING
    assert result.missing == ("backpack",)


def test_recovers_when_item_comes_back():
    s = Smoother(["backpack"])
    feed(s, 1, [A] * 15)
    assert feed(s, 1, [P] * 8).verdict is Verdict.OK


def test_lists_only_missing_items_in_required_order():
    s = Smoother(["coat", "boots", "glasses"])
    for _ in range(5):
        result = s.update({1: {"coat": A, "boots": P, "glasses": A}})[1]
    assert result.verdict is Verdict.MISSING
    assert result.missing == ("coat", "glasses")


def test_unverifiable_is_not_missing():
    s = Smoother(["coat", "boots"])
    for _ in range(10):
        result = s.update({1: {"coat": P, "boots": U}})[1]
    assert result.verdict is Verdict.UNVERIFIABLE
    assert result.items["boots"] is Decision.UNVERIFIABLE


def test_people_are_smoothed_independently():
    s = Smoother(["backpack"])
    for _ in range(6):
        results = s.update({1: {"backpack": P}, 2: {"backpack": A}})
    assert results[1].verdict is Verdict.OK
    assert results[2].verdict is Verdict.MISSING


def test_stale_tracks_are_dropped():
    s = Smoother(["backpack"], max_missed_frames=3)
    feed(s, 1, [A] * 5)
    for _ in range(3):
        s.update({})
    assert 1 in s.tracked_ids
    s.update({})
    assert 1 not in s.tracked_ids
    assert feed(s, 1, [A]).verdict is Verdict.PENDING  # starts from scratch


def test_tie_goes_to_present():
    assert decide([P, P, P, A, A, A], min_samples=5) is Decision.PRESENT


def test_invalid_min_samples():
    with pytest.raises(ValueError):
        Smoother(["backpack"], window=5, min_samples=6)
