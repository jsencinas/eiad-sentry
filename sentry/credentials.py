"""Identify students by the matrícula printed on their Tec credential.

Pure logic, no OCR engine, OpenCV or database, so it is tested on its own:

- `find_matriculas`: pick student IDs (A0 + 7 digits, or L0 + 7 digits) out
  of OCR text, fixing the usual OCR confusions (O/0, I/1, S/5, B/8...).
- `assign_reads`: give each read to the person holding the card (the text's
  center inside their box), or to the only person in view.
- `IdentityTracker`: one state per tracked person. A matrícula is accepted
  after `confirm_reads` matching reads within `window` seconds, so a single
  OCR misread never identifies anyone. It also reports who left the frame.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from sentry.association import Box, Person

# A0/L0 + 7 digits. After the letter, OCR often swaps look-alike characters.
_CANDIDATE = re.compile(r"[AL][0-9OQDIL|!SBZG]{8}")
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "!": "1",
                           "S": "5", "B": "8", "Z": "2", "G": "6"})


def find_matriculas(text: str) -> list[str]:
    """Student IDs found in one piece of OCR text, normalized (e.g. "AO1234S67" -> "A01234567")."""
    compact = re.sub(r"[\s\-.:_]", "", text.upper())
    found = []
    for m in _CANDIDATE.finditer(compact):
        candidate = m.group()[0] + m.group()[1:].translate(_TO_DIGIT)
        if candidate[1] == "0" and candidate[2:].isdigit():
            found.append(candidate)
    return found


@dataclass(frozen=True)
class TextRead:
    """One piece of text from the OCR, with its box in frame coordinates."""

    text: str
    box: Box


def assign_reads(reads: Iterable[TextRead], persons: Sequence[Person]) -> dict[int, list[str]]:
    """Matrículas per track ID: the card's center must be inside the person's box.

    With a single person in view, any matrícula read is theirs (the card is
    often held in front of the body, partly outside the box).
    """
    out: dict[int, list[str]] = {}
    for read in reads:
        ids = find_matriculas(read.text)
        if not ids:
            continue
        cx, cy = read.box.center
        holders = [p for p in persons if p.box.contains(cx, cy)]
        if len(holders) != 1 and len(persons) == 1:
            holders = list(persons)
        if len(holders) == 1:
            out.setdefault(holders[0].track_id, []).extend(ids)
    return out


@dataclass
class _Candidate:
    reads: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class IdentityTracker:
    """Who is who: unidentified people wait for a confirmed matrícula."""

    confirm_reads: int = 2
    window: float = 10.0
    clock: Callable[[], float] = time.monotonic
    identities: dict[int, str] = field(default_factory=dict)
    _pending: dict[int, _Candidate] = field(default_factory=dict, repr=False)

    def needs_credential(self, track_ids: Iterable[int]) -> list[int]:
        return [t for t in track_ids if t not in self.identities]

    def add_reads(self, reads: Mapping[int, Sequence[str]]) -> dict[int, str]:
        """Feed OCR reads per track; return the identities confirmed just now."""
        now = self.clock()
        confirmed = {}
        for track_id, ids in reads.items():
            if track_id in self.identities:
                continue
            cand = self._pending.setdefault(track_id, _Candidate())
            cand.reads = [(m, t) for m, t in cand.reads if now - t <= self.window]
            cand.reads.extend((m, now) for m in ids)
            for matricula in dict.fromkeys(m for m, _ in cand.reads):
                if sum(1 for m, _ in cand.reads if m == matricula) >= self.confirm_reads:
                    self.identities[track_id] = matricula
                    self._pending.pop(track_id, None)
                    confirmed[track_id] = matricula
                    break
        return confirmed

    def forget_missing(self, tracked_ids: Iterable[int]) -> dict[int, str]:
        """Drop people no longer tracked; return the identified ones who left."""
        still = set(tracked_ids)
        left = {t: m for t, m in self.identities.items() if t not in still}
        for t in left:
            del self.identities[t]
        for t in [t for t in self._pending if t not in still]:
            del self._pending[t]
        return left
