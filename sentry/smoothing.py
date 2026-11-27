"""Temporal smoothing of per-frame PPE statuses and per-person verdicts.

A single bad frame must not trigger an alert: each (person, item) keeps a
sliding window of recent statuses and decides by majority vote, and only
once it has enough verifiable samples. Pure Python, no OpenCV/YOLO.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence

from sentry.association import Status


class Decision(Enum):
    """Smoothed state of one item on one person."""

    PENDING = "pending"  # not enough samples yet
    PRESENT = "present"
    ABSENT = "absent"
    UNVERIFIABLE = "unverifiable"


class Verdict(Enum):
    OK = "OK"
    MISSING = "MISSING"
    UNVERIFIABLE = "UNVERIFIABLE"
    PENDING = "PENDING"


@dataclass(frozen=True)
class PersonResult:
    track_id: int
    verdict: Verdict
    missing: tuple[str, ...]
    items: Mapping[str, Decision]


def decide(samples: Sequence[Status], min_samples: int) -> Decision:
    """Majority vote over a window of per-frame statuses."""
    counts = Counter(samples)
    present = counts[Status.PRESENT]
    absent = counts[Status.ABSENT]
    unverifiable = counts[Status.UNVERIFIABLE]
    if len(samples) >= min_samples and unverifiable > present + absent:
        return Decision.UNVERIFIABLE
    if present + absent < min_samples:
        return Decision.PENDING
    # A tie goes to PRESENT: better to miss one alert than to raise a false one.
    return Decision.PRESENT if present >= absent else Decision.ABSENT


def verdict_for(items: Mapping[str, Decision], required: Sequence[str]) -> tuple[Verdict, tuple[str, ...]]:
    missing = tuple(i for i in required if items[i] is Decision.ABSENT)
    if missing:
        return Verdict.MISSING, missing
    values = set(items.values())
    if Decision.PENDING in values:
        return Verdict.PENDING, ()
    if Decision.UNVERIFIABLE in values:
        return Verdict.UNVERIFIABLE, ()
    return Verdict.OK, ()


@dataclass
class _Track:
    windows: dict[str, deque[Status]]
    missed_frames: int = 0


@dataclass
class Smoother:
    """Keeps one vote window per tracked person and item."""

    required: Sequence[str]
    window: int = 15
    min_samples: int = 5
    max_missed_frames: int = 30
    _tracks: dict[int, _Track] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.min_samples <= self.window:
            raise ValueError("min_samples must be between 1 and window")

    def update(self, observations: Mapping[int, Mapping[str, Status]]) -> dict[int, PersonResult]:
        """Feed one frame of statuses; return results for the people in this frame."""
        for track_id in list(self._tracks):
            if track_id not in observations:
                track = self._tracks[track_id]
                track.missed_frames += 1
                if track.missed_frames > self.max_missed_frames:
                    del self._tracks[track_id]

        results: dict[int, PersonResult] = {}
        for track_id, statuses in observations.items():
            track = self._tracks.get(track_id)
            if track is None:
                track = _Track({i: deque(maxlen=self.window) for i in self.required})
                self._tracks[track_id] = track
            track.missed_frames = 0
            for item in self.required:
                track.windows[item].append(statuses.get(item, Status.ABSENT))
            items = {i: decide(track.windows[i], self.min_samples) for i in self.required}
            verdict, missing = verdict_for(items, self.required)
            results[track_id] = PersonResult(track_id, verdict, missing, items)
        return results

    @property
    def tracked_ids(self) -> set[int]:
        return set(self._tracks)
