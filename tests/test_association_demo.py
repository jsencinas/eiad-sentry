"""Demo rule: the PPE center must fall inside the person box."""

from sentry.association import Box, CenterInBoxRule, Detection, Person, Status, assign_ppe, owners

RULE = CenterInBoxRule()
REQUIRED = ["backpack"]
FRAME_H = 480


def statuses(persons, detections):
    return assign_ppe(persons, detections, REQUIRED, RULE, FRAME_H)


def test_center_inside_box_is_present():
    person = Person(1, Box(100, 50, 250, 400))
    bag = Detection("backpack", Box(180, 150, 260, 260))  # center (220, 205), edge sticks out
    assert statuses([person], [bag]) == {1: {"backpack": Status.PRESENT}}


def test_center_outside_box_is_absent():
    person = Person(1, Box(100, 50, 250, 400))
    bag = Detection("backpack", Box(240, 150, 340, 260))  # overlaps, but center (290, 205) is outside
    assert statuses([person], [bag]) == {1: {"backpack": Status.ABSENT}}


def test_no_detection_is_absent():
    assert statuses([Person(1, Box(0, 0, 100, 300))], []) == {1: {"backpack": Status.ABSENT}}


def test_other_labels_are_ignored():
    person = Person(1, Box(0, 0, 100, 300))
    cup = Detection("cup", Box(40, 100, 60, 120))
    assert statuses([person], [cup]) == {1: {"backpack": Status.ABSENT}}


def test_ppe_counts_for_one_person_only():
    # Overlapping people: the bag is inside both boxes but belongs to the closer one.
    left = Person(1, Box(0, 0, 200, 400))
    right = Person(2, Box(150, 0, 350, 400))
    bag = Detection("backpack", Box(170, 150, 210, 200))  # center x=190: inside both
    result = statuses([left, right], [bag])
    assert result[2]["backpack"] is Status.PRESENT
    assert result[1]["backpack"] is Status.ABSENT
    assert owners([left, right], [bag], RULE)[bag] == 2


def test_no_people_means_no_results():
    assert statuses([], [Detection("backpack", Box(0, 0, 10, 10))]) == {}
