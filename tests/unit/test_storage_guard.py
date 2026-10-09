import datetime as dt
import sqlite3

import pytest

from robustlab.core.models import DataPartition
from robustlab.oos import holdout_guard
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso

D = dt.date
PART = DataPartition(holdout_from=D(2024, 7, 1), holdout_to=D(2026, 6, 30), embargo_days=10)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "x.sqlite")
    yield d
    d.close()


# --- Holdout Guard -----------------------------------------------------------------
@pytest.mark.parametrize(
    "frm, to, ok",
    [
        (D(2023, 1, 1), D(2024, 6, 20), True),  # ends the day before embargo starts
        (D(2023, 1, 1), D(2024, 6, 21), False),  # touches first embargo day (closed interval)
        (D(2024, 6, 21), D(2024, 6, 22), False),
        (D(2025, 1, 1), D(2025, 2, 1), False),  # inside holdout
        (D(2026, 7, 10), D(2026, 8, 1), False),  # last embargo day
        (D(2026, 7, 11), D(2026, 8, 1), True),
        (D(2020, 1, 1), D(2027, 1, 1), False),  # spans the holdout
    ],
)
def test_overlap_boundaries(frm, to, ok):
    assert holdout_guard.check_overlap(frm, to, PART).approved is ok


def test_guard_logs_every_decision_and_pins_partition(db):
    kw = dict(request_hash="r", partition=PART, partition_hash="h1")
    assert holdout_guard.evaluate(db, from_date=D(2023, 1, 1), to_date=D(2023, 2, 1), **kw).approved
    assert not holdout_guard.evaluate(db, from_date=D(2025, 1, 1), to_date=D(2025, 2, 1), **kw).approved
    changed = holdout_guard.evaluate(
        db, from_date=D(2023, 1, 1), to_date=D(2023, 2, 1), request_hash="r", partition=PART, partition_hash="h2"
    )
    assert not changed.approved and changed.reason.startswith("PARTITION_CHANGED")
    rows = db.all("SELECT approved, reason FROM holdout_access_log ORDER BY id")
    assert [r["approved"] for r in rows] == [1, 0, 0]


# --- append-only ----------------------------------------------------------------------
def test_append_only_tables_reject_update_and_delete(db):
    db.insert(
        "strategy_version",
        {
            "strategy_version_id": "sv_1", "strategy_name": "x", "expert": "e", "ex5_sha256": "a",
            "mq5_sha256": None, "source_available": 0, "telemetry_version": "1", "inputs_json": "[]",
            "registered_at": now_iso(),
        },
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.conn.execute("UPDATE strategy_version SET strategy_name='y'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.conn.execute("DELETE FROM strategy_version")
    db.insert("strategy_version", {"strategy_version_id": "sv_1", "strategy_name": "z", "expert": "e",
                                   "ex5_sha256": "a", "mq5_sha256": None, "source_available": 0,
                                   "telemetry_version": "1", "inputs_json": "[]", "registered_at": now_iso()},
              if_absent=True)
    assert db.one("SELECT strategy_name FROM strategy_version")["strategy_name"] == "x"


def test_reopen_keeps_schema(tmp_path):
    Database(tmp_path / "y.sqlite").close()
    Database(tmp_path / "y.sqlite").close()


# --- artifacts ---------------------------------------------------------------------------
def test_artifacts_are_content_addressed_and_deduplicated(db, tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", db)
    a = store.put_bytes(b"hello")
    b = store.put_bytes(b"hello")
    assert a == b and store.read(a) == b"hello"
    assert store.path(a).parent.name == a[:2]
    assert db.one("SELECT COUNT(*) AS n FROM artifact")["n"] == 1
    store.path(a).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupted"):
        store.read(a)
