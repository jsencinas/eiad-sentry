"""Read credentials with OCR in a background thread.

OCR takes about 1 s per frame on the Pi's CPU (the Coral keeps doing the
detection meanwhile), so it never runs in the detection loop:

- the loop `submit()`s a frame, plus the people in it, at most once per
  `interval` and only while someone still has to show their credential;
- the worker reads the newest submitted frame and drops older ones;
- the loop picks up finished reads with `results()`.

The OCR engine is RapidOCR (pip install rapidocr onnxruntime), loaded in the
worker the first time it is needed. Tests pass their own `engine`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Sequence

from sentry.association import Box, Person
from sentry.credentials import TextRead

Engine = Callable[[object], list[TextRead]]


def rapidocr_engine() -> Engine:
    """Build the RapidOCR engine: image -> text pieces with their boxes."""
    try:
        from rapidocr import RapidOCR
    except ImportError:
        raise SystemExit(
            '--credential needs OCR: pip install onnxruntime && pip install --no-deps rapidocr '
            "(see the README for its other dependencies)"
        ) from None
    ocr = RapidOCR()

    def read(image) -> list[TextRead]:
        out = ocr(image)
        if out.boxes is None or out.txts is None:
            return []
        reads = []
        for quad, text in zip(out.boxes, out.txts):
            xs = [float(p[0]) for p in quad]
            ys = [float(p[1]) for p in quad]
            reads.append(TextRead(str(text), Box(min(xs), min(ys), max(xs), max(ys))))
        return reads

    return read


class CredentialReader:
    """Runs OCR on the newest submitted frame, off the detection loop."""

    def __init__(
        self,
        interval: float = 1.0,
        engine_factory: Callable[[], Engine] = rapidocr_engine,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        self.interval = interval
        self._engine_factory = engine_factory
        self._clock = clock
        self._log = log
        self._cond = threading.Condition()
        self._job: tuple[object, list[Person]] | None = None
        self._done: list[tuple[list[TextRead], list[Person]]] = []
        self._busy = False
        self._last_submit = float("-inf")
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> "CredentialReader":
        self._thread = threading.Thread(target=self._run, name="credential-ocr", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def due(self) -> bool:
        """True when a new frame may be submitted (rate limit, and no OCR in progress)."""
        with self._cond:
            return not self._busy and self._job is None and self._clock() - self._last_submit >= self.interval

    def submit(self, frame, persons: Sequence[Person]) -> None:
        """Queue a frame for OCR (replaces any frame still waiting)."""
        with self._cond:
            self._job = (frame, list(persons))
            self._last_submit = self._clock()
            self._cond.notify_all()

    def results(self) -> list[tuple[list[TextRead], list[Person]]]:
        """Finished reads since the last call: (text pieces, people in that frame)."""
        with self._cond:
            done, self._done = self._done, []
        return done

    def run_once(self, engine: Engine) -> bool:
        """Process the waiting frame, if any (used by the worker and by tests)."""
        with self._cond:
            job, self._job = self._job, None
            self._busy = job is not None
        if job is None:
            return False
        frame, persons = job
        try:
            reads = engine(frame)
        except Exception as e:  # noqa: BLE001 - a bad frame must not stop the reader
            self._log(f"[credential] OCR failed: {e}")
            reads = []
        with self._cond:
            self._done.append((reads, persons))
            self._busy = False
        return True

    def _run(self) -> None:
        engine = self._engine_factory()
        self._log("[credential] OCR ready")
        while True:
            with self._cond:
                while self._job is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
            self.run_once(engine)
