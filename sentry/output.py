"""Headless output: annotated frames, violation hook and periodic stats."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Collection, Mapping, Sequence

import cv2

from sentry.association import Detection, Person
from sentry.cooldown import Cooldown
from sentry.smoothing import PersonResult, Verdict

# BGR colors
COLORS = {
    Verdict.OK: (60, 200, 60),
    Verdict.MISSING: (40, 40, 230),
    Verdict.UNVERIFIABLE: (0, 200, 230),
    Verdict.PENDING: (170, 170, 170),
}
PPE_COLOR = (230, 160, 40)
UNASSIGNED_COLOR = (120, 120, 120)


def _label(img, text: str, x: int, y: int, color) -> None:
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
    (w, h), base = cv2.getTextSize(text, font, scale, thick)
    y = max(y, h + base + 2)
    cv2.rectangle(img, (x, y - h - base - 2), (x + w + 4, y), color, -1)
    cv2.putText(img, text, (x + 2, y - base), font, scale, (255, 255, 255), thick, cv2.LINE_AA)


WAITING_TEXT = "Muestre su credencial"


def person_text(
    result: PersonResult | None, track_id: int, name: str | None = None, waiting: bool = False
) -> str:
    """Label for a person: "#3 A01234567 OK", "#3 MISSING: coat", "#3 Muestre su credencial"."""
    who = f"#{track_id} {name}" if name else f"#{track_id}"
    if waiting:
        return f"{who} {WAITING_TEXT}"
    if result is None:
        return who
    if result.verdict is Verdict.MISSING:
        return f"{who} MISSING: {', '.join(result.missing)}"
    return f"{who} {result.verdict.value}"


def annotate(
    frame,
    persons: Sequence[Person],
    ppe: Sequence[Detection],
    results: Mapping[int, PersonResult],
    owners: Mapping[Detection, int | None],
    header: str = "",
    waiting: Collection[int] = (),
    names: Mapping[int, str] | None = None,
):
    """Return a copy of `frame` with people, PPE and verdicts drawn on it.

    People in `waiting` still have to show their credential; `names` maps
    identified people to their matrícula.
    """
    names = names or {}
    img = frame.copy()
    for det in ppe:
        b = det.box
        color = PPE_COLOR if owners.get(det) is not None else UNASSIGNED_COLOR
        cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 1)
        _label(img, f"{det.label} {det.conf:.2f}", int(b.x1), int(b.y1), color)
    for p in persons:
        result = results.get(p.track_id)
        is_waiting = p.track_id in waiting
        if is_waiting:
            color = COLORS[Verdict.UNVERIFIABLE]
        else:
            color = COLORS[result.verdict] if result else COLORS[Verdict.PENDING]
        b = p.box
        cv2.rectangle(img, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), color, 2)
        text = person_text(result, p.track_id, names.get(p.track_id), is_waiting)
        _label(img, text, int(b.x1), int(b.y1), color)
    if header:
        _label(img, header, 0, img.shape[0], (40, 40, 40))
    return img


def on_violation(person_id: int, missing: Sequence[str], frame, save_dir: Path | None = None) -> Path | None:
    """Hook called when a person is missing PPE (already rate-limited per person).

    It prints and saves the annotated frame, and returns the photo's path.
    With --db, the CLI also records the alert in the lab-monitor database.
    This is the place to connect the alarm (relay, buzzer, LED) later.
    """
    stamp = datetime.now()
    print(f"[ALERT] {stamp:%Y-%m-%d %H:%M:%S} person #{person_id} missing: {', '.join(missing)}")
    if save_dir is None:
        return None
    save_dir.mkdir(parents=True, exist_ok=True)
    path = save_dir / f"{stamp:%Y%m%d_%H%M%S}_id{person_id}_{'-'.join(missing).replace(' ', '_')}.jpg"
    if cv2.imwrite(str(path), frame):
        print(f"[ALERT] saved {path}")
        return path
    print(f"[ALERT] could not write {path}")
    return None


@dataclass
class AlertDispatcher:
    """Calls the violation hook for MISSING people, at most once per cooldown each."""

    cooldown: Cooldown
    save_dir: Path | None = None
    hook: Callable[..., object] = on_violation

    def __call__(self, results: Mapping[int, PersonResult], annotated_frame) -> None:
        for track_id, result in results.items():
            if result.verdict is Verdict.MISSING and self.cooldown.ready(track_id):
                self.hook(track_id, list(result.missing), annotated_frame, self.save_dir)


@dataclass
class StatsPrinter:
    """Prints analysis fps and inference time every `interval` seconds."""

    interval: float = 5.0
    clock: Callable[[], float] = time.monotonic
    _start: float = field(default=0.0, init=False)
    _frames: int = field(default=0, init=False)
    _infer: list[float] = field(default_factory=list, init=False)
    last_line: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self._start = self.clock()

    def update(self, inference_ms: float, results: Mapping[int, PersonResult]) -> None:
        self._frames += 1
        self._infer.append(inference_ms)
        now = self.clock()
        elapsed = now - self._start
        if elapsed < self.interval:
            return
        fps = self._frames / elapsed
        avg = sum(self._infer) / len(self._infer)
        people = ", ".join(person_text(r, t) for t, r in sorted(results.items())) or "none"
        self.last_line = f"{fps:.1f} fps | inference {avg:.0f} ms avg, {max(self._infer):.0f} ms max"
        print(f"[stats] {self.last_line} | people: {people}")
        self._start, self._frames, self._infer = now, 0, []
