import codecs
import datetime as dt

import io

import pyarrow.parquet as pq
import pytest

from robustlab.core.models import InputSpec, InputType, JobStatus, TickModel
from robustlab.core.models import TesterSettings as _Settings
from robustlab.core.params import normalize_params
from robustlab.mt5 import ini_builder, telemetry_reader as tr
from tests.telemetry_factory import FIXTURE_DEALS, env_doc, stats_values, write_telemetry

JOB = "jb_20261009120000000_abcdef01"
TESTER = _Settings(model=TickModel.REAL_TICKS, deposit=10000, currency="USD", leverage=100)
EXP = tr.Expectation(
    job_id=JOB, telemetry_version="1", symbol="EURUSD", timeframe="H1",
    from_date=dt.date(2023, 1, 9), to_date=dt.date(2023, 1, 13), tester=TESTER, coverage_tolerance_days=7,
)


# --- .set / ini --------------------------------------------------------------------------
def test_set_matches_hardware_r1_format():
    inputs = [
        InputSpec(name="InpInt", type=InputType.INT),
        InputSpec(name="InpDouble", type=InputType.DOUBLE),
        InputSpec(name="InpString", type=InputType.STRING),
        InputSpec(name="InpBool", type=InputType.BOOL),
        InputSpec(name="InpEnum", type=InputType.ENUM),
        InputSpec(name="InpDate", type=InputType.DATETIME),
    ]
    params = {"InpInt": 42, "InpDouble": 1.23456789012345, "InpString": "日本語_abc",
              "InpBool": True, "InpEnum": 15, "InpDate": 1672531200}
    text = ini_builder.build_set_text(inputs, normalize_params(params, inputs), JOB)
    # Same lines as the .set that passed every type correctly on hardware (R1), harness line last.
    assert text == (
        "InpInt=42||42||1||42||N\r\n"
        "InpDouble=1.23456789012345||1.23456789012345||0.1||1.23456789012345||N\r\n"
        "InpString=日本語_abc\r\n"
        "InpBool=true||true||0||true||N\r\n"
        "InpEnum=15||15||0||15||N\r\n"
        "InpDate=1672531200||1672531200||0||1672531200||N\r\n"
        f"RL_JobId={JOB}\r\n"
    )
    raw = ini_builder.encode_set(text)
    assert raw.startswith(codecs.BOM_UTF16_LE) and raw[2:].decode("utf-16-le") == text


def test_ini_is_ascii_without_credentials():
    ini = ini_builder.build_ini_text(
        expert="RobustLab\\RL_SmokeTest", set_filename=f"rl_{JOB}.set", symbol="EURUSD", timeframe="H1",
        from_date=dt.date(2023, 1, 9), to_date=dt.date(2023, 1, 13), tester=TESTER, job_id=JOB,
    )
    lines = ini.split("\r\n")
    assert lines[0] == "[Tester]" and "Model=4" in lines and "FromDate=2023.01.09" in lines
    assert "ToDate=2023.01.13" in lines and "Deposit=10000" in lines and "ShutdownTerminal=1" in lines
    assert "Optimization=0" in lines and "ForwardMode=0" in lines and "UseCloud=0" in lines
    assert not any(l.lower().startswith(("login", "password", "server")) for l in lines)


@pytest.mark.parametrize("bad", ["[Common]\r\nLogin=1\r\n", "[Tester]\r\nPassword=x\r\n", "[Tester]\r\nExpert=é\r\n"])
def test_unsafe_ini_rejected(bad):
    with pytest.raises(ValueError):
        ini_builder.assert_safe_ini(bad)


# --- telemetry validation --------------------------------------------------------------------
def test_real_hardware_deals_pass_all_checks(tmp_path):
    write_telemetry(tmp_path, JOB)
    res = tr.validate(tmp_path, EXP)
    assert res.status is JobStatus.SUCCEEDED, res.problems
    assert len(res.deals) == 65 and str(res.net_profit_sum) == "9.69"
    # end-of-test closing deals have magic 0 and must still be counted (F4)
    assert sum(1 for r in res.deals if r["comment"] == "end of test" and r["magic"] == "0") == 2


def test_content_hash_ignores_row_order_and_parquet_has_metadata(tmp_path):
    write_telemetry(tmp_path / "a", JOB)
    text = FIXTURE_DEALS.read_text(encoding="utf-8").splitlines()
    shuffled = "\n".join([text[0]] + list(reversed(text[1:]))) + "\n"
    write_telemetry(tmp_path / "b", JOB, deals_text=shuffled)
    a, b = tr.validate(tmp_path / "a", EXP), tr.validate(tmp_path / "b", EXP)
    assert a.deals_content_hash == b.deals_content_hash
    table = pq.read_table(io.BytesIO(tr.deals_parquet_bytes(a.deals, a.deals_content_hash)))
    assert table.num_rows == 65
    assert table.schema.metadata[b"robustlab.content_sha256"].decode() == a.deals_content_hash


def _bad(tmp_path, **kw):
    write_telemetry(tmp_path, JOB, **kw)
    return tr.validate(tmp_path, EXP)


def test_missing_marker_is_telemetry_missing(tmp_path):
    assert _bad(tmp_path, skip=("done",)).status is JobStatus.TELEMETRY_MISSING


@pytest.mark.parametrize(
    "kw, expected",
    [
        (dict(skip=("stats",)), "missing"),
        (dict(done={"job_id": "jb_other", "telemetry_version": "1", "deals_total": 65, "trade_deals": 64}), "job_id"),
        (dict(done={"job_id": JOB, "telemetry_version": "2", "deals_total": 65, "trade_deals": 64}), "telemetry_version"),
        (dict(done={"job_id": JOB, "telemetry_version": "1", "deals_total": 66, "trade_deals": 64}), "deals_total"),
        (dict(stats=stats_values(deals=63.0)), "STAT_DEALS"),
        (dict(stats=stats_values(profit=9.71)), "STAT_PROFIT"),
        (dict(env=env_doc(JOB, mql_tester=False)), "Strategy Tester"),
        (dict(env=env_doc(JOB, account_leverage=50)), "leverage"),
        (dict(env=env_doc(JOB, period=16388)), "period"),
        (dict(env=env_doc(JOB, first_tick="2023.01.20 00:00:00")), "DATA_COVERAGE"),
        (dict(env=env_doc(JOB, ticks=0)), "DATA_COVERAGE"),
        (dict(env=env_doc(JOB, last_tick="2023.01.13 10:00:00")), "PERIOD_SEMANTICS"),
        (dict(deals_text="ticket,time\n1,2\n"), "PARSE"),
    ],
)
def test_inconsistent_telemetry_is_quarantined(tmp_path, kw, expected):
    res = _bad(tmp_path, **kw)
    assert res.status is JobStatus.QUARANTINED
    assert any(expected in p for p in res.problems), res.problems


def test_coverage_tolerance_is_configurable(tmp_path):
    write_telemetry(tmp_path, JOB, env=env_doc(JOB, first_tick="2023.01.11 00:00:00"))
    strict = tr.Expectation(**{**EXP.__dict__, "coverage_tolerance_days": 1})
    assert tr.validate(tmp_path, strict).status is JobStatus.QUARANTINED
    assert tr.validate(tmp_path, EXP).status is JobStatus.SUCCEEDED
