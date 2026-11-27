import pytest

from sentry.association import Box, Person
from sentry.credentials import IdentityTracker, TextRead, assign_reads, find_matriculas


@pytest.mark.parametrize(
    "text, expected",
    [
        ("A01234567", ["A01234567"]),
        ("Matricula: A01234567", ["A01234567"]),
        ("a0 1234 567", ["A01234567"]),          # spaces and lower case
        ("AO1234S67", ["A01234567"]),            # O for 0, S for 5
        ("A0I23456B", ["A01234568"]),            # I for 1, B for 8
        ("L01234567", ["L01234567"]),
        ("TECNOLOGICO DE MONTERREY", []),
        ("A1234567", []),                         # too short
        ("B01234567", []),                        # not a student prefix
        ("#300 MISSING: cell phone", []),
    ],
)
def test_matriculas_are_found_and_normalized(text, expected):
    assert find_matriculas(text) == expected


def person(track_id, x1, x2):
    return Person(track_id, Box(x1, 0, x2, 400))


def card_at(x, text="A01234567"):
    return TextRead(text, Box(x - 20, 100, x + 20, 120))


def test_card_goes_to_the_person_holding_it():
    persons = [person(1, 0, 200), person(2, 250, 450)]
    assert assign_reads([card_at(300)], persons) == {2: ["A01234567"]}


def test_card_between_two_people_is_ignored():
    persons = [person(1, 0, 200), person(2, 250, 450)]
    assert assign_reads([card_at(225)], persons) == {}


def test_with_one_person_any_card_is_theirs():
    assert assign_reads([card_at(500)], [person(7, 0, 200)]) == {7: ["A01234567"]}


def test_text_without_a_matricula_is_ignored():
    assert assign_reads([card_at(100, "TECNOLOGICO")], [person(1, 0, 200)]) == {}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_one_read_is_not_enough():
    ids = IdentityTracker(clock=Clock())
    assert ids.add_reads({1: ["A01234567"]}) == {}
    assert ids.needs_credential([1]) == [1]


def test_two_matching_reads_identify_the_person():
    clock = Clock()
    ids = IdentityTracker(clock=clock)
    ids.add_reads({1: ["A01234567"]})
    clock.t = 1.0
    assert ids.add_reads({1: ["A01234567"]}) == {1: "A01234567"}
    assert ids.needs_credential([1, 2]) == [2]


def test_a_misread_does_not_count_towards_another_id():
    clock = Clock()
    ids = IdentityTracker(clock=clock)
    ids.add_reads({1: ["A01234567"]})
    clock.t = 1.0
    assert ids.add_reads({1: ["A01234561"]}) == {}


def test_reads_too_far_apart_do_not_confirm():
    clock = Clock()
    ids = IdentityTracker(window=10.0, clock=clock)
    ids.add_reads({1: ["A01234567"]})
    clock.t = 15.0
    assert ids.add_reads({1: ["A01234567"]}) == {}


def test_people_who_leave_are_reported_once():
    clock = Clock()
    ids = IdentityTracker(clock=clock)
    ids.add_reads({1: ["A01234567"], 2: ["A07654321"]})
    ids.add_reads({1: ["A01234567"], 2: ["A07654321"]})
    assert ids.forget_missing([2]) == {1: "A01234567"}
    assert ids.forget_missing([2]) == {}
    assert ids.identities == {2: "A07654321"}
