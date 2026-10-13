"""`rlab jobs startlog`: the masked digest of a job's terminal log (F8: no login / IP printed)."""

from __future__ import annotations

from typer.testing import CliRunner

from robustlab.cli.main import app


def test_startlog_shows_known_lines_only(env):
    out = env.run()
    assert out.status == "SUCCEEDED"
    res = CliRunner().invoke(app, ["jobs", "startlog", "last", "--workspace", str(env.ws)])
    assert res.exit_code == 0, res.output
    assert "automatic testing started" in res.output and "build" in res.output
