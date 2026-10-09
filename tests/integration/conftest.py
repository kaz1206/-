import stat
import sys
import textwrap
from pathlib import Path

import pytest

from robustlab.single_backtest import run_backtest

FAKE = Path(__file__).with_name("fake_terminal.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "mt5"
    (data / "MQL5" / "Experts" / "RobustLab").mkdir(parents=True)
    (data / "MQL5" / "Experts" / "RobustLab" / "RL_SmokeTest.ex5").write_bytes(b"ex5-binary")
    # the fake takes its data dir from argv[0], so it is linked into the fake terminal folder
    (data / "fake_terminal.py").symlink_to(FAKE)
    term = data / "terminal64.exe"
    term.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{data / "fake_terminal.py"}" "$@"\n')
    term.chmod(term.stat().st_mode | stat.S_IEXEC)
    common = tmp_path / "common"
    common.mkdir()
    monkeypatch.setenv("FAKE_COMMON_DIR", str(common))
    cfg = tmp_path / "configs"
    (cfg / "strategies").mkdir(parents=True)
    (cfg / "requests").mkdir()
    (cfg / "terminal.yaml").write_text(textwrap.dedent(f"""
        terminal_path: {term}
        common_files_dir: {common}
        start_timeout_sec: 2
        run_timeout_sec: 4
        poll_interval_sec: 0.05
    """))
    (cfg / "partition.yaml").write_text("holdout_from: 2024-07-01\nholdout_to: 2026-06-30\nembargo_days: 10\n")
    (cfg / "strategies" / "smoke.yaml").write_text(textwrap.dedent("""
        strategy_name: RL_SmokeTest
        expert: RobustLab\\RL_SmokeTest
        telemetry_version: "1"
        inputs:
          - {name: InpFastPeriod, type: int}
          - {name: InpSlowPeriod, type: int}
          - {name: InpLots, type: double}
    """))
    req = cfg / "requests" / "r.yaml"
    req.write_text(textwrap.dedent("""
        strategy_file: ../strategies/smoke.yaml
        symbol: EURUSD
        timeframe: H1
        from_date: 2023-01-09
        to_date: 2023-01-13
        params: {InpFastPeriod: 12, InpSlowPeriod: 48, InpLots: 0.01}
        tester: {model: REAL_TICKS, deposit: 10000, currency: USD, leverage: 100}
    """))

    class E:
        pass

    e = E()
    e.data, e.common, e.req, e.ws = data, common, req, tmp_path / "workspace"
    e.terminal_cfg, e.partition = cfg / "terminal.yaml", cfg / "partition.yaml"

    def run(mode="ok", rerun=False, req=None):
        monkeypatch.setenv("FAKE_MODE", mode)
        return run_backtest(req or e.req, e.terminal_cfg, e.partition, e.ws, rerun=rerun)

    e.run = run
    return e


