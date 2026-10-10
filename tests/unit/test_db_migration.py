import sqlite3

from robustlab.storage.db import SCHEMA_VERSION, Database


def test_v1_database_is_migrated_to_v2(tmp_path):
    path = tmp_path / "w.sqlite"
    Database(path).close()
    con = sqlite3.connect(path)
    con.execute("DROP TABLE run_metrics")
    con.execute("UPDATE schema_version SET version = 1")
    con.commit()
    con.close()
    db = Database(path)
    try:
        assert db.one("SELECT version FROM schema_version")["version"] == SCHEMA_VERSION == 2
        assert db.one("SELECT name FROM sqlite_master WHERE name = 'run_metrics'") is not None
    finally:
        db.close()
