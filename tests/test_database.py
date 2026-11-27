import pytest

from sentry.database import (
    NO_SIGNAL,
    DatabaseWriter,
    LiveState,
    incident_params,
    live_state,
    parse_db_map,
)
from sentry.smoothing import PersonResult, Verdict

BOTH = {"glasses": "glasses", "coat": "coat"}


def person(track_id, verdict, missing=()):
    return PersonResult(track_id, verdict, tuple(missing), {})


# --- item mapping ---------------------------------------------------------


def test_real_items_map_to_themselves():
    assert parse_db_map(None, ["glasses", "coat"]) == BOTH


def test_demo_item_is_mapped_explicitly():
    assert parse_db_map("cell phone=glasses", ["cell phone"]) == {"cell phone": "glasses"}


def test_unmapped_demo_item_fails_with_an_example():
    with pytest.raises(ValueError, match='--db-map "backpack=glasses"'):
        parse_db_map(None, ["backpack"])


@pytest.mark.parametrize(
    "text, message",
    [
        ("cell phone=boots", "not one of"),
        ("cup=glasses", "not a required item"),
        ("cell phone", "should look like"),
    ],
)
def test_bad_mappings_are_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        parse_db_map(text, ["cell phone"])


# --- live state -----------------------------------------------------------


def test_nobody_in_view():
    assert live_state({}, BOTH) == LiveState(0, 0, 0, False, "CONFIRMADA")


def test_counts_who_lacks_each_item():
    results = {
        1: person(1, Verdict.OK),
        2: person(2, Verdict.MISSING, ["glasses"]),
        3: person(3, Verdict.MISSING, ["glasses", "coat"]),
    }
    assert live_state(results, BOTH) == LiveState(3, 2, 1, False, "CONFIRMADA")


def test_everyone_ok_is_protected():
    results = {1: person(1, Verdict.OK), 2: person(2, Verdict.OK)}
    assert live_state(results, BOTH) == LiveState(2, 0, 0, True, "CONFIRMADA")


def test_undecided_people_make_it_pending():
    results = {1: person(1, Verdict.OK), 2: person(2, Verdict.PENDING)}
    assert live_state(results, BOTH).status == "PENDIENTE"
    assert not live_state(results, BOTH).protected


def test_a_confirmed_absence_is_not_pending():
    results = {1: person(1, Verdict.PENDING), 2: person(2, Verdict.MISSING, ["coat"])}
    assert live_state(results, BOTH).status == "CONFIRMADA"


# --- incidents ------------------------------------------------------------


def test_incident_says_what_is_missing():
    assert incident_params(7, ["coat"], BOTH, "/out/a.jpg") == (7, False, True, "/out/a.jpg")
    assert incident_params(8, ["glasses", "coat"], BOTH, None) == (8, True, True, None)


def test_demo_item_is_recorded_as_its_database_item():
    assert incident_params(3, ["cell phone"], {"cell phone": "glasses"}, None) == (3, True, False, None)


# --- writer ---------------------------------------------------------------


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.db.fail:
            raise OSError("connection refused")
        table = "incidencias" if "INSERT" in sql else "estado_laboratorio"
        self.db.writes.append((table, params))


class FakeDB:
    def __init__(self):
        self.writes = []
        self.fail = False
        self.connections = 0

    def connect(self, dsn):
        if self.fail:
            raise OSError("connection refused")
        self.connections += 1
        return self

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass

    def live_writes(self):
        return [p for t, p in self.writes if t == "estado_laboratorio"]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_writer(item_map=BOTH):
    db, clock, logs = FakeDB(), Clock(), []
    writer = DatabaseWriter("dsn", item_map, refresh=2.0, stale_after=5.0,
                            connect=db.connect, clock=clock, log=logs.append)
    return writer, db, clock, logs


def test_live_state_is_written_on_change_and_as_a_heartbeat():
    writer, db, clock, _ = make_writer()
    writer.live({1: person(1, Verdict.OK)})
    writer.step()
    writer.step()  # nothing changed, heartbeat not due yet
    assert len(db.live_writes()) == 1

    writer.live({1: person(1, Verdict.MISSING, ["coat"])})
    writer.step()  # changed: written at once
    clock.t = 1.9
    writer.live({1: person(1, Verdict.MISSING, ["coat"])})
    writer.step()
    clock.t = 2.5
    writer.live({1: person(1, Verdict.MISSING, ["coat"])})
    writer.step()  # heartbeat
    assert len(db.live_writes()) == 3
    assert db.live_writes()[-1] == LiveState(1, 0, 1, False, "CONFIRMADA").params()


def test_no_frames_means_no_signal():
    writer, db, clock, _ = make_writer()
    writer.live({1: person(1, Verdict.OK)})
    writer.step()
    clock.t = 6.0  # stream stalled: no live() for longer than stale_after
    writer.step()
    assert db.live_writes()[-1] == NO_SIGNAL.params()


def test_incidents_are_inserted_with_what_is_missing():
    writer, db, _, _ = make_writer({"cell phone": "glasses"})
    writer.incident(4, ["cell phone"], "/out/x.jpg")
    writer.step()
    assert ("incidencias", (4, True, False, "/out/x.jpg")) in db.writes


def test_database_outage_keeps_incidents_and_retries():
    writer, db, _, logs = make_writer()
    db.fail = True
    writer.incident(1, ["glasses"])
    writer.live({1: person(1, Verdict.MISSING, ["glasses"])})
    writer.step()
    writer.step()
    assert db.writes == []
    assert logs == ["[db] write failed, will retry: connection refused"]  # logged once

    db.fail = False
    writer.step()
    assert ("incidencias", (1, True, False, None)) in db.writes
    assert db.live_writes() == [LiveState(1, 1, 0, False, "CONFIRMADA").params()]
    assert logs[-1] == "[db] Writing again"


def test_close_flushes_and_marks_no_signal():
    writer, db, _, _ = make_writer()
    writer.live({1: person(1, Verdict.OK)})
    writer.incident(2, ["coat"])
    writer.close()
    assert ("incidencias", (2, False, True, None)) in db.writes
    assert db.live_writes()[-1] == NO_SIGNAL.params()
