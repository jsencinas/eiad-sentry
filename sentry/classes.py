"""Map logical item names to the class ids of a YOLO model, failing early.

The script must not assume which classes a model has: it reads them from
`model.names` and stops at startup with a clear message if one is missing.
"""

from __future__ import annotations

from typing import Mapping, Sequence


class ClassNameError(Exception):
    pass


def normalize_names(names: Mapping[int, str] | Sequence[str]) -> dict[int, str]:
    """`model.names` may be a dict {id: name} or a list; return a dict."""
    if isinstance(names, Mapping):
        return {int(k): str(v) for k, v in names.items()}
    return dict(enumerate(map(str, names)))


def class_ids(names: Mapping[int, str] | Sequence[str], wanted: Mapping[str, str], model_label: str = "model") -> dict[str, int]:
    """Resolve {logical name: model class name} to {logical name: class id}.

    Matching ignores case and surrounding spaces. Raises ClassNameError listing
    the classes the model has and the ones that are missing.
    """
    by_name = {v.strip().lower(): k for k, v in normalize_names(names).items()}
    resolved: dict[str, int] = {}
    missing: list[str] = []
    for logical, class_name in wanted.items():
        cid = by_name.get(class_name.strip().lower())
        if cid is None:
            missing.append(class_name if class_name == logical else f"{class_name} (for {logical})")
        else:
            resolved[logical] = cid
    if missing:
        available = ", ".join(sorted(normalize_names(names).values()))
        raise ClassNameError(
            f"The {model_label} does not have the class(es): {', '.join(missing)}.\n"
            f"Classes it has: {available}"
        )
    return resolved
