import pytest

from sentry.cli import build_parser


def parse(*extra):
    return build_parser().parse_args(["--source", "0", *extra])


def test_frames_are_not_rotated_by_default():
    assert parse().rotate == 0


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_quarter_turns_are_accepted(degrees):
    assert parse("--rotate", str(degrees)).rotate == degrees


def test_other_angles_are_rejected():
    with pytest.raises(SystemExit):
        parse("--rotate", "45")
