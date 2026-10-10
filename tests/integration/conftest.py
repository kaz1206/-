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


@pytest.fixture
def opt(env, monkeypatch):
    cfg = env.req.parent.parent
    (cfg / "strategies" / "smoke3.yaml").write_text(textwrap.dedent("""
        strategy_name: RL_SmokeTest
        expert: RobustLab\\RL_SmokeTest
        telemetry_version: "3"
        inputs:
          - {name: InpFastPeriod, type: int}
          - {name: InpSlowPeriod, type: int}
          - {name: InpLots, type: double}
    """))

    def study(name="s.yaml", max_passes=2000, fast=(5, 1, 7), slow=(20, 10, 40), period=("2022-01-03", "2023-01-02")):
        p = cfg / name
        p.write_text(textwrap.dedent(f"""
            study_id: test_grid
            strategy_file: strategies/smoke3.yaml
            symbol: EURUSD
            timeframe: H1
            from_date: {period[0]}
            to_date: {period[1]}
            param_space:
              InpFastPeriod: {{start: {fast[0]}, step: {fast[1]}, stop: {fast[2]}}}
              InpSlowPeriod: {{start: {slow[0]}, step: {slow[1]}, stop: {slow[2]}}}
            fixed: {{InpLots: 0.01}}
            tester: {{model: OHLC_M1, deposit: 10000, currency: USD, leverage: 100}}
            max_passes_per_job: {max_passes}
        """))
        return p

    def run(path=None, mode="ok", rerun=False):
        monkeypatch.setenv("FAKE_MODE", "ok")
        monkeypatch.setenv("FAKE_OPT_MODE", mode)
        from robustlab.optimization import run_optimization

        return run_optimization(path or study(), env.terminal_cfg, env.partition, env.ws, rerun=rerun)

    env.study, env.run_opt = study, run
    return env
