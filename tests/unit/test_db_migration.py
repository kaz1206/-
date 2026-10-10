import sqlite3

import pytest

from robustlab.storage.db import SCHEMA_VERSION, Database


@pytest.mark.parametrize(("old", "dropped"), [(1, ["run_metrics", "trial_ledger"]), (2, ["trial_ledger", "opt_job"])])
def test_old_database_is_migrated(tmp_path, old, dropped):
    path = tmp_path / "w.sqlite"
    Database(path).close()
    con = sqlite3.connect(path)
    for t in dropped:
        con.execute(f"DROP TABLE {t}")
    con.execute("UPDATE schema_version SET version = ?", (old,))
    con.commit()
    con.close()
    db = Database(path)
    try:
        assert db.one("SELECT version FROM schema_version")["version"] == SCHEMA_VERSION == 3
        for t in dropped:
            assert db.one("SELECT name FROM sqlite_master WHERE name = ?", (t,)) is not None
    finally:
        db.close()


def test_research_tables_of_p3_are_append_only(tmp_path):
    db = Database(tmp_path / "w.sqlite")
    try:
        names = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for t in ("optimization_round", "opt_job_artifact", "pass_result", "trial_ledger"):
            assert {f"{t}_no_update", f"{t}_no_delete"} <= names
        with pytest.raises(ValueError):
            db.update_job("x", table="pass_result", status="X")
    finally:
        db.close()
