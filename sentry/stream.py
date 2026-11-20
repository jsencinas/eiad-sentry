"""Read a video source in a background thread, keeping only the newest frame.

If YOLO is slower than the camera, reading frames in the main loop would let
OpenCV queue up old frames and the analysis would fall further and further
behind. The reader thread drains the source continuously and the detector
always gets the most recent frame. Network streams and webcams reconnect
automatically if they drop.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import cv2


def parse_source(source: str) -> int | str:
    """"0" -> webcam index 0; anything else (URL or file path) stays a string."""
    return int(source) if source.isdigit() else source


def is_file_source(source: int | str) -> bool:
    return isinstance(source, str) and "://" not in source and Path(source).is_file()


class LatestFrameReader:
    def __init__(self, source: str, reconnect_delay: float = 2.0) -> None:
        self.source = parse_source(source)
        self.is_file = is_file_source(self.source)
        self.reconnect_delay = reconnect_delay
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stream-reader", daemon=True)

    # -- public API ---------------------------------------------------------
    def start(self) -> "LatestFrameReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=5)

    @property
    def finished(self) -> bool:
        """True when a video file reached its end (live sources never finish)."""
        return self._finished.is_set()

    def read(self, last_seq: int, timeout: float = 1.0):
        """Wait for a frame newer than `last_seq`. Returns (seq, frame) or (last_seq, None)."""
        with self._cond:
            self._cond.wait_for(
                lambda: self._seq != last_seq or self._stop.is_set() or self._finished.is_set(),
                timeout=timeout,
            )
            if self._seq == last_seq:
                return last_seq, None
            return self._seq, self._frame

    # -- thread -------------------------------------------------------------
    def _open(self) -> cv2.VideoCapture | None:
        if isinstance(self.source, str) and "://" in self.source:
            # Give up on a hung network stream after 5 s instead of FFmpeg's default 30 s.
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000]
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, params)
        else:
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return None
        if not self.is_file:
            # Ask the backend for a minimal internal queue (ignored by some backends).
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        cap = None
        frame_delay = 0.0
        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    if self.is_file:
                        print(f"[stream] Could not open file {self.source}")
                        self._finished.set()
                        return
                    print(f"[stream] Could not open {self.source}, retrying in {self.reconnect_delay:.0f} s")
                    self._stop.wait(self.reconnect_delay)
                    continue
                print(f"[stream] Connected to {self.source}")
                if self.is_file:
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    # Play files in real time so they behave like a live camera.
                    frame_delay = 1.0 / fps if fps and fps > 0 else 0.0

            ok, frame = cap.read()
            if not ok:
                cap.release()
                cap = None
                if self.is_file:
                    print("[stream] End of file")
                    self._finished.set()
                    with self._cond:
                        self._cond.notify_all()
                    return
                print(f"[stream] Stream lost, reconnecting in {self.reconnect_delay:.0f} s")
                self._stop.wait(self.reconnect_delay)
                continue

            with self._cond:
                self._frame = frame
                self._seq += 1
                self._cond.notify_all()
            if frame_delay:
                time.sleep(frame_delay)

        if cap is not None:
            cap.release()
