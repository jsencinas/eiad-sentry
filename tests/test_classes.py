import pytest

from sentry.classes import ClassNameError, class_ids

COCO_LIKE = {0: "person", 24: "backpack", 39: "bottle"}


def test_resolves_ids_ignoring_case():
    assert class_ids(COCO_LIKE, {"person": "Person", "backpack": "backpack"}) == {"person": 0, "backpack": 24}


def test_accepts_list_of_names():
    assert class_ids(["person", "car"], {"person": "person"}) == {"person": 0}


def test_missing_class_lists_available_and_missing():
    with pytest.raises(ClassNameError) as err:
        class_ids(COCO_LIKE, {"person": "person", "cup": "cup"})
    message = str(err.value)
    assert "cup" in message
    assert "backpack, bottle, person" in message
