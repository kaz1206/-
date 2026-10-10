"""Telemetry v2 validation: daily series and EA tracking values (P2_PLAN §4)."""

import dataclasses
import datetime as dt

import pytest

from robustlab.core.models import JobStatus, TickModel
from robustlab.core.models import TesterSettings as _Settings
from robustlab.mt5 import telemetry_reader as tr
from tests.telemetry_factory import (
    FIXTURE_DEALS,
    TRACKING,
    daily_text_from_deals,
    env_doc,
    write_telemetry,
)

JOB = "jb_20261010120000000_abcdef01"
EXP = tr.Expectation(
    job_id=JOB, telemetry_version="2", symbol="EURUSD", timeframe="H1",
    from_date=dt.date(2023, 1, 9), to_date=dt.date(2023, 1, 13),
    tester=_Settings(model=TickModel.REAL_TICKS, deposit=10000, currency="USD", leverage=100),
    coverage_tolerance_days=7,
)
GOOD_DAILY = daily_text_from_deals(FIXTURE_DEALS.read_text(encoding="utf-8"))


def test_v2_passes_and_reads_daily(tmp_path):
    write_telemetry(tmp_path, JOB, version="2")
    res = tr.validate(tmp_path, EXP)
    assert res.status is JobStatus.SUCCEEDED, res.problems
    assert len(res.daily) == 4 and res.daily[-1]["equity_close"] == "10009.69"


@pytest.mark.parametrize(
    "daily, expected",
    [
        (GOOD_DAILY.replace("10009.69,10009.69", "10009.69,10012.00"), "final equity"),
        ("\n".join([GOOD_DAILY.splitlines()[0], *reversed(GOOD_DAILY.splitlines()[1:])]) + "\n", "strictly increasing"),
        (GOOD_DAILY.replace("2023.01.09", "2023.01.06"), "outside"),
        (GOOD_DAILY.splitlines()[0] + "\n", "empty"),
        ("date,x\n2023.01.09,1\n", "PARSE"),
    ],
)
def test_bad_daily_is_quarantined(tmp_path, daily, expected):
    write_telemetry(tmp_path, JOB, version="2", daily_text=daily)
    res = tr.validate(tmp_path, EXP)
    assert res.status is JobStatus.QUARANTINED
    assert any(expected in p for p in res.problems), res.problems


def test_missing_daily_file_or_tracking(tmp_path):
    write_telemetry(tmp_path / "a", JOB, version="2", skip=("daily",))
    assert "missing" in " ".join(tr.validate(tmp_path / "a", EXP).problems)
    write_telemetry(tmp_path / "b", JOB, version="2", env=env_doc(JOB, telemetry_version="2"))
    assert any("tracking" in p for p in tr.validate(tmp_path / "b", EXP).problems)


def test_unknown_telemetry_version(tmp_path):
    write_telemetry(tmp_path, JOB, version="2")
    res = tr.validate(tmp_path, dataclasses.replace(EXP, telemetry_version="9"))
    assert res.status is JobStatus.QUARANTINED and "unsupported" in res.problems[0]


def test_tracking_constants_are_complete():
    assert set(tr.TRACKING_KEYS) <= set(TRACKING)
