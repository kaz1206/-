import json

from typer.testing import CliRunner

from robustlab.cli.main import app
from tests.integration.test_single_backtest import env  # noqa: F401  (fixture)

runner = CliRunner()


def _run(env, *extra):
    return runner.invoke(app, ["backtest", "run", str(env.req), "--terminal", str(env.terminal_cfg),
                               "--partition", str(env.partition), "--workspace", str(env.ws), *extra])


def test_cli_run_show_list_and_exit_codes(env, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    res = _run(env, "--json")
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["status"] == "SUCCEEDED" and out["deals"] == 65

    show = runner.invoke(app, ["backtest", "show", out["run_id"], "--workspace", str(env.ws)])
    assert show.exit_code == 0
    assert "[2023-01-09, 2023-01-13) broker server time" in show.output
    assert "artifact log_segment:" in show.output
    assert "authorized" not in show.output  # log contents are never printed (F8)

    lst = runner.invoke(app, ["jobs", "list", "--workspace", str(env.ws)])
    assert lst.exit_code == 0 and "SUCCEEDED" in lst.output

    monkeypatch.setenv("FAKE_MODE", "bad_profit")
    assert _run(env, "--rerun").exit_code == 2


def test_cli_guard_exit_code(env):
    bad = env.req.with_name("h.yaml")
    bad.write_text(env.req.read_text().replace("2023-01-09", "2025-01-06").replace("2023-01-13", "2025-01-10"))
    res = runner.invoke(app, ["backtest", "run", str(bad), "--terminal", str(env.terminal_cfg),
                              "--partition", str(env.partition), "--workspace", str(env.ws)])
    assert res.exit_code == 3 and "GUARD_REJECTED" in res.output
