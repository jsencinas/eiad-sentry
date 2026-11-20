"""YOLOv8 inference + tracking, converted to the plain types of `association`.

Two layouts are supported with the same interface:
- one model detects both people and PPE (demo mode: COCO `yolov8n.pt`);
- a person model (tracked) plus a separate PPE model (real mode).
Only the person detections are tracked: they carry the per-person ID used
for smoothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping

from sentry.association import Box, Detection, Person


@dataclass
class FrameResult:
    persons: list[Person] = field(default_factory=list)
    ppe: list[Detection] = field(default_factory=list)
    inference_ms: float = 0.0


def load_model(path: str):
    """Load a YOLO model (Ultralytics downloads official weights the first time)."""
    from ultralytics import YOLO  # imported lazily: tests never need it

    return YOLO(path)


def _boxes(result):
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.tolist()
    cls = boxes.cls.int().tolist()
    conf = boxes.conf.tolist()
    ids = boxes.id.int().tolist() if boxes.id is not None else [None] * len(xyxy)
    return list(zip(xyxy, cls, conf, ids))


class YoloDetector:
    def __init__(
        self,
        person_model,
        person_class: int,
        ppe_model,
        ppe_classes: Mapping[int, str],
        conf: float = 0.4,
        imgsz: int = 640,
        tracker: str = "bytetrack.yaml",
    ) -> None:
        """
        person_class: class id of "person" in `person_model`.
        ppe_classes: class id in `ppe_model` -> logical item name (e.g. {24: "backpack"}).
        Pass the same object as `person_model` and `ppe_model` to run a single model.
        """
        self.person_model = person_model
        self.person_class = person_class
        self.ppe_model = ppe_model
        self.ppe_classes = dict(ppe_classes)
        self.single_model = person_model is ppe_model
        self.conf = conf
        self.imgsz = imgsz
        self.tracker = tracker

    def __call__(self, frame) -> FrameResult:
        out = FrameResult()
        start = time.perf_counter()
        if self.single_model:
            classes = [self.person_class, *self.ppe_classes]
            tracked = self._track(frame, classes)
            self._collect(out, tracked, people=True, ppe=True)
        else:
            tracked = self._track(frame, [self.person_class])
            predicted = self.ppe_model.predict(
                frame, classes=list(self.ppe_classes), conf=self.conf, imgsz=self.imgsz, verbose=False
            )[0]
            self._collect(out, tracked, people=True, ppe=False)
            self._collect(out, predicted, people=False, ppe=True)
        out.inference_ms = (time.perf_counter() - start) * 1000
        return out

    def _track(self, frame, classes):
        return self.person_model.track(
            frame,
            persist=True,
            classes=classes,
            conf=self.conf,
            imgsz=self.imgsz,
            tracker=self.tracker,
            verbose=False,
        )[0]

    def _collect(self, out: FrameResult, result, people: bool, ppe: bool) -> None:
        for (x1, y1, x2, y2), cls, conf, track_id in _boxes(result):
            box = Box(x1, y1, x2, y2)
            if people and cls == self.person_class:
                # People the tracker has not confirmed yet have no ID: skip them.
                if track_id is not None:
                    out.persons.append(Person(track_id, box))
            elif ppe and cls in self.ppe_classes:
                out.ppe.append(Detection(self.ppe_classes[cls], box, conf))
