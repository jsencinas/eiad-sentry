"""Assign PPE detections to people and decide, per frame, what each person wears.

Pure Python: no OpenCV, NumPy or Ultralytics, so it is testable without a
camera or a model. Coordinates are pixels with the origin at the top-left.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass(frozen=True)
class Person:
    track_id: int
    box: Box


@dataclass(frozen=True)
class Detection:
    """A PPE detection. `label` is the logical item name (e.g. "coat")."""

    label: str
    box: Box
    conf: float = 1.0


class Status(Enum):
    """What a single frame says about one item on one person."""

    PRESENT = "present"
    ABSENT = "absent"
    UNVERIFIABLE = "unverifiable"


class AssociationRule(Protocol):
    def matches(self, person: Box, item: str, ppe: Box) -> bool:
        """Whether a PPE box of type `item` may belong to `person`."""

    def unverifiable(self, person: Box, item: str, frame_height: float) -> bool:
        """Whether a missing `item` simply cannot be seen on this person."""


class CenterInBoxRule:
    """Demo rule: the PPE belongs to a person if its center is inside the person box."""

    def matches(self, person: Box, item: str, ppe: Box) -> bool:
        return person.contains(*ppe.center)

    def unverifiable(self, person: Box, item: str, frame_height: float) -> bool:
        return False


def _owner(persons: Sequence[Person], det: Detection, rule: AssociationRule) -> Person | None:
    """Pick the single person a detection belongs to, or None.

    When several boxes match (people overlapping), the PPE goes to the person
    whose horizontal center is closest, relative to their width.
    """
    candidates = [p for p in persons if rule.matches(p.box, det.label, det.box)]
    if not candidates:
        return None
    cx = det.box.center[0]
    return min(candidates, key=lambda p: abs(p.box.center[0] - cx) / max(p.box.width, 1e-6))


def assign_ppe(
    persons: Sequence[Person],
    detections: Iterable[Detection],
    required: Sequence[str],
    rule: AssociationRule,
    frame_height: float,
) -> dict[int, dict[str, Status]]:
    """Return, for every person, the per-frame status of each required item."""
    worn: dict[int, set[str]] = {p.track_id: set() for p in persons}
    for det in detections:
        if det.label not in required:
            continue
        owner = _owner(persons, det, rule)
        if owner is not None:
            worn[owner.track_id].add(det.label)

    result: dict[int, dict[str, Status]] = {}
    for p in persons:
        statuses: dict[str, Status] = {}
        for item in required:
            if item in worn[p.track_id]:
                statuses[item] = Status.PRESENT
            elif rule.unverifiable(p.box, item, frame_height):
                statuses[item] = Status.UNVERIFIABLE
            else:
                statuses[item] = Status.ABSENT
        result[p.track_id] = statuses
    return result


def owners(
    persons: Sequence[Person], detections: Iterable[Detection], rule: AssociationRule
) -> Mapping[Detection, int | None]:
    """Owner track id of each detection (for drawing), or None if unassigned."""
    return {d: (o.track_id if (o := _owner(persons, d, rule)) else None) for d in detections}
