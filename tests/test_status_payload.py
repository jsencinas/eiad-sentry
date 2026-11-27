from sentry.cli import build_parser, status_payload
from sentry.smoothing import PersonResult, Verdict


def result(track_id, verdict, missing=()):
    return PersonResult(track_id, verdict, tuple(missing), {})


def test_waiting_people_are_reported_without_a_ppe_verdict():
    results = {1: result(1, Verdict.MISSING, ["cell phone"]), 2: result(2, Verdict.OK)}
    people = status_payload(results, "", waiting=[1], names={2: "A01234567"})["people"]
    assert people == [
        {"id": 1, "verdict": "SIN_CREDENCIAL", "missing": [], "matricula": None},
        {"id": 2, "verdict": "OK", "missing": [], "matricula": "A01234567"},
    ]


def test_credential_check_is_off_by_default():
    assert build_parser().parse_args(["--source", "0"]).credential is False
    assert build_parser().parse_args(["--source", "0", "--credential"]).credential is True
