import textwrap

import pytest

from robustlab.config.loader import (
    ConfigError,
    load_partition,
    load_request,
    load_terminal_config,
)


def write(p, text):
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


STRATEGY = """
strategy_name: RL_SmokeTest
expert: RobustLab\\RL_SmokeTest
telemetry_version: "1"
inputs:
  - {name: InpFastPeriod, type: int}
  - {name: InpSlowPeriod, type: int}
  - {name: InpLots, type: double}
"""

REQUEST = """
strategy_file: strategy.yaml
symbol: EURUSD
timeframe: H1
from_date: 2023-01-09
to_date: 2023-01-13
params: {InpFastPeriod: 12, InpSlowPeriod: 48, InpLots: 0.01}
tester: {model: REAL_TICKS, deposit: 10000, currency: USD, leverage: 100}
"""


def test_load_request_ok(tmp_path):
    write(tmp_path / "strategy.yaml", STRATEGY)
    lr = load_request(write(tmp_path / "req.yaml", REQUEST))
    assert lr.strategy.strategy_name == "RL_SmokeTest"
    assert lr.request.tester.model.value == "REAL_TICKS"
    assert len(lr.request_hash) == 64


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("timeframe: H1", "timeframe: H5"),
        ("to_date: 2023-01-13", "to_date: 2023-01-09"),
        ("model: REAL_TICKS", "model: EVERY_TICK"),
        ("leverage: 100}", "leverage: 100, execution_delay_ms: 5}"),
        ("leverage: 100}", "leverage: 100, password: x}"),
        ("symbol: EURUSD", "symbol: EURUSD\nlogin: 123"),
        ("symbol: EURUSD", "symbol: EURUSD\nunknown_key: 1"),
    ],
)
def test_load_request_rejects(tmp_path, mutation, message):
    write(tmp_path / "strategy.yaml", STRATEGY)
    bad = REQUEST.replace(mutation, message)
    with pytest.raises(ConfigError):
        load_request(write(tmp_path / "req.yaml", bad))


def test_strategy_expert_must_not_have_extension(tmp_path):
    write(tmp_path / "strategy.yaml", STRATEGY.replace("expert: RobustLab\\RL_SmokeTest", "expert: RobustLab\\RL_SmokeTest.ex5"))
    with pytest.raises(ConfigError):
        load_request(write(tmp_path / "req.yaml", REQUEST))


def test_terminal_and_partition(tmp_path):
    t = load_terminal_config(write(tmp_path / "t.yaml", "terminal_path: C:/MT5/terminal64.exe\ncommon_files_dir: C:/x\n"))
    assert t.start_timeout_sec == 300 and t.run_timeout_sec == 3600  # F7 defaults
    p, h = load_partition(write(tmp_path / "p.yaml", "holdout_from: 2024-07-01\nholdout_to: 2026-06-30\nembargo_days: 10\n"))
    p2, h2 = load_partition(write(tmp_path / "p2.yaml", "embargo_days: 10\nholdout_to: 2026-06-30\nholdout_from: 2024-07-01\n"))
    assert h == h2 and p.embargo_days == 10
    _, h3 = load_partition(write(tmp_path / "p3.yaml", "holdout_from: 2024-07-02\nholdout_to: 2026-06-30\nembargo_days: 10\n"))
    assert h3 != h
