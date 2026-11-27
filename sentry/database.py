"""Write alerts and the live lab state to the lab-monitor PostgreSQL database.

Two tables of the lab-monitor dashboard (migration 004) are written:

- ``incidencias``: one row per alert, saying what the person was missing
  (safety glasses, lab coat or both) and where the photo was saved.
- ``estado_laboratorio`` (row 1): how many people are in view and how many
  lack each item, refreshed every few seconds and whenever it changes.

The database only knows two items, ``glasses`` and ``coat``. In demo mode the
detector checks stand-in objects (e.g. "cell phone"), so each required item
is mapped to one of them with ``--db-map "cell phone=glasses"``.

All database work happens in a background thread: a slow or unreachable
database never delays detection, and failed writes are retried.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Collection, Mapping, Sequence

from sentry.smoothing import PersonResult, Verdict

DB_ITEMS = ("glasses", "coat")

INSERT_INCIDENT = """
    INSERT INTO public.incidencias (track_id, falta_lentes, falta_bata, foto, persona_id)
    VALUES (%s, %s, %s, %s, %s)
"""

# Credential check (migration 005): students by matrícula and their entries.
UPSERT_PERSONA = """
    INSERT INTO public.personas (nombre, matricula) VALUES (%s, %s)
    ON CONFLICT (matricula) DO UPDATE SET matricula = EXCLUDED.matricula
    RETURNING id
"""
OPEN_ACCESO = "INSERT INTO public.accesos (persona_id) VALUES (%s) RETURNING id"
CLOSE_ACCESO = "UPDATE public.accesos SET salida = now() WHERE id = %s AND salida IS NULL"

UPDATE_LIVE = """
    UPDATE public.estado_laboratorio
    SET personas = %s,
        proteccion = %s,
        cantidad_personas = %s,
        sin_lentes = %s,
        sin_bata = %s,
        deteccion_estado = %s,
        ultima_deteccion = CASE WHEN %s THEN now() ELSE ultima_deteccion END
    WHERE id = 1
"""
UPDATE_LIVE_WITH_CREDENTIALS = UPDATE_LIVE.replace(
    "    WHERE id = 1", "        , sin_credencial = %s\n    WHERE id = 1"
)


def parse_db_map(text: str | None, required: Sequence[str]) -> dict[str, tuple[str, ...]]:
    """Map each required detector item to the database items it stands for.

    `text` is "item=glasses,other=coat"; "item=glasses+coat" makes one demo
    object stand for both (a person without it lacks glasses and coat).
    Items already called glasses or coat map to themselves. Every required
    item must end up mapped.
    """
    mapping = {item: (item,) for item in required if item in DB_ITEMS}
    for pair in (text or "").split(","):
        if not pair.strip():
            continue
        item, sep, target = (s.strip() for s in pair.partition("="))
        if not sep or not item or not target:
            raise ValueError(f'--db-map: "{pair.strip()}" should look like "item=glasses"')
        targets = tuple(dict.fromkeys(t.strip() for t in target.split("+")))
        bad = [t for t in targets if t not in DB_ITEMS]
        if bad:
            raise ValueError(f'--db-map: "{bad[0]}" is not one of: {", ".join(DB_ITEMS)}')
        if item not in required:
            raise ValueError(f'--db-map: "{item}" is not a required item ({", ".join(required)})')
        mapping[item] = targets
    unmapped = [i for i in required if i not in mapping]
    if unmapped:
        raise ValueError(
            f"--db-map: say which database item {', '.join(repr(i) for i in unmapped)} "
            f'stands for, e.g. --db-map "{unmapped[0]}=glasses"'
        )
    return mapping


@dataclass(frozen=True)
class LiveState:
    """One row of estado_laboratorio, as written by the detector."""

    people: int
    without_glasses: int
    without_coat: int
    protected: bool  # people in view and every one of them is OK
    status: str  # CONFIRMADA, PENDIENTE or SIN_SENAL
    without_credential: int = 0  # still have to show their credential

    def params(self, credentials: bool = False) -> tuple:
        values = (
            self.people > 0,
            self.protected,
            self.people,
            self.without_glasses,
            self.without_coat,
            self.status,
            self.people > 0,  # refresh ultima_deteccion only when someone is seen
        )
        return values + (self.without_credential,) if credentials else values


NO_SIGNAL = LiveState(0, 0, 0, False, "SIN_SENAL")


def missing_db_items(missing: Sequence[str], item_map: Mapping[str, Sequence[str]]) -> set[str]:
    return {target for i in missing if i in item_map for target in item_map[i]}


def live_state(
    results: Mapping[int, PersonResult],
    item_map: Mapping[str, Sequence[str]],
    unidentified: Collection[int] = (),
) -> LiveState:
    """Summarize the people in the current frame.

    People in `unidentified` (credential check on, not identified yet) are
    counted but not judged: their PPE only counts once they are identified.
    """
    judged = [r for t, r in results.items() if t not in unidentified]
    waiting = sum(1 for t in results if t in unidentified)
    verdicts = [r.verdict for r in judged]
    lacking = [missing_db_items(r.missing, item_map) for r in judged if r.verdict is Verdict.MISSING]
    undecided = waiting > 0 or any(v in (Verdict.PENDING, Verdict.UNVERIFIABLE) for v in verdicts)
    if results and not lacking and undecided:
        status = "PENDIENTE"
    else:
        status = "CONFIRMADA"
    return LiveState(
        people=len(results),
        without_glasses=sum("glasses" in m for m in lacking),
        without_coat=sum("coat" in m for m in lacking),
        protected=bool(results) and not waiting and all(v is Verdict.OK for v in verdicts),
        status=status,
        without_credential=waiting,
    )


def incident_params(track_id: int, missing: Sequence[str], item_map: Mapping[str, Sequence[str]], photo) -> tuple | None:
    """Parameters for INSERT_INCIDENT, or None if nothing the database tracks is missing."""
    lacking = missing_db_items(missing, item_map)
    if not lacking:
        return None
    return (track_id, "glasses" in lacking, "coat" in lacking, str(photo) if photo else None)


def _psycopg_connect(dsn: str):
    try:
        import psycopg
    except ImportError:
        raise SystemExit('--db needs psycopg: pip install "psycopg[binary]"') from None
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5)


class DatabaseWriter:
    """Queues incidents, identities and live-state updates and writes them from a thread.

    With `credentials`, identified students are recorded in personas/accesos
    and their incidents carry their persona_id (needs migration 005).
    """

    def __init__(
        self,
        dsn: str,
        item_map: Mapping[str, Sequence[str]],
        credentials: bool = False,
        refresh: float = 2.0,
        stale_after: float = 5.0,
        connect: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        self.dsn = dsn
        self.item_map = dict(item_map)
        self.credentials = credentials
        self.refresh = refresh
        self.stale_after = stale_after
        self._connect = connect or _psycopg_connect
        self._clock = clock
        self._log = log
        self._conn = None
        self._ops: queue.Queue[tuple] = queue.Queue(maxsize=1000)  # written in order
        self._accesos: dict[int, tuple[int, int]] = {}  # track_id -> (persona_id, acceso_id)
        self._lock = threading.Lock()
        self._live: LiveState | None = None
        self._live_at = clock()
        self._written: LiveState | None = None
        self._written_at = float("-inf")
        self._last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- called from the detection loop (never blocks) ---

    def live(self, results: Mapping[int, PersonResult], unidentified: Collection[int] = ()) -> None:
        state = live_state(results, self.item_map, unidentified)
        with self._lock:
            self._live, self._live_at = state, self._clock()

    def incident(self, track_id: int, missing: Sequence[str], photo=None) -> None:
        params = incident_params(track_id, missing, self.item_map, photo)
        if params is not None:
            self._queue(("incident", track_id, params))

    def identified(self, track_id: int, matricula: str) -> None:
        """A student showed their credential: record the person and their entry."""
        self._queue(("identified", track_id, matricula))

    def departed(self, track_id: int) -> None:
        """An identified student left the frame: close their entry."""
        self._queue(("departed", track_id, None))

    def _queue(self, op: tuple) -> None:
        try:
            self._ops.put_nowait(op)
        except queue.Full:
            self._report(f"write queue full, dropping a {op[0]} record")

    # --- background thread ---

    def start(self) -> "DatabaseWriter":
        self._thread = threading.Thread(target=self._run, name="db-writer", daemon=True)
        self._thread.start()
        return self

    def close(self, timeout: float = 5.0) -> None:
        """Flush pending incidents, mark the detector as gone and disconnect."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        try:
            self._flush_ops()
            for track_id in list(self._accesos):  # the detector stops: everyone's entry ends
                self._apply(("departed", track_id, None))
            self._write_live(NO_SIGNAL)
        except Exception as e:  # noqa: BLE001 - shutting down anyway
            self._report(f"could not write the final state: {e}")
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self.step()

    def step(self) -> None:
        """One write cycle: pending records in order, then the live state if due."""
        try:
            self._flush_ops()
            with self._lock:
                state, at = self._live, self._live_at
            now = self._clock()
            if state is None or now - at > self.stale_after:
                state = NO_SIGNAL  # no frames lately: the stream or the detector stalled
            if state != self._written or now - self._written_at >= self.refresh:
                self._write_live(state)
                self._written, self._written_at = state, now
            if self._last_error:
                self._log("[db] Writing again")
                self._last_error = ""
        except Exception as e:  # noqa: BLE001 - any driver error: reconnect next cycle
            self._report(f"write failed, will retry: {e}")
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None

    def _cursor(self):
        if self._conn is None:
            self._conn = self._connect(self.dsn)
        return self._conn.cursor()

    def _flush_ops(self) -> None:
        while True:
            try:
                op = self._ops.queue[0]  # peek: only drop it once written
            except IndexError:
                return
            self._apply(op)
            self._ops.get_nowait()

    def _apply(self, op: tuple) -> None:
        kind, track_id, data = op
        with self._cursor() as cur:
            if kind == "incident":
                persona_id = self._accesos.get(track_id, (None, None))[0]
                cur.execute(INSERT_INCIDENT, data + (persona_id,))
            elif kind == "identified":
                # New students get their matrícula as name until someone edits it.
                cur.execute(UPSERT_PERSONA, (data, data))
                persona_id = cur.fetchone()[0]
                cur.execute(OPEN_ACCESO, (persona_id,))
                self._accesos[track_id] = (persona_id, cur.fetchone()[0])
            elif kind == "departed":
                entry = self._accesos.get(track_id)
                if entry is not None:
                    cur.execute(CLOSE_ACCESO, (entry[1],))
                    del self._accesos[track_id]

    def _write_live(self, state: LiveState) -> None:
        sql = UPDATE_LIVE_WITH_CREDENTIALS if self.credentials else UPDATE_LIVE
        with self._cursor() as cur:
            cur.execute(sql, state.params(self.credentials))
            if cur.rowcount != 1:
                raise RuntimeError("estado_laboratorio has no row with id = 1")

    def _report(self, message: str) -> None:
        first_line = message.splitlines()[0] if message else message
        if first_line != self._last_error:  # log each distinct error once
            self._log(f"[db] {first_line}")
            self._last_error = first_line
