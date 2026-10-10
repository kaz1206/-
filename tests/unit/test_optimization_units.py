"""Grid expansion, optimization .set/ini, tester log, XML and pass metrics (P3)."""

import csv
import datetime as dt
from pathlib import Path

import pytest

from robustlab.core.models import InputSpec, StrategySpec, StudyRequest
from robustlab.generation.grid import GridError, build_grid, match_combo
from robustlab.metrics.pass_metrics import compute_pass
from robustlab.mt5 import ini_builder
from robustlab.mt5.optimization_io import (
    XmlReportError,
    read_opt_report,
    read_tester_log,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "hw_20261010_p3"
TESTER = {"model": "OHLC_M1", "deposit": 10000, "currency": "USD", "leverage": 100}


def strategy(*inputs):
    return StrategySpec(strategy_name="S", expert="RobustLab\\S", telemetry_version="3",
                        inputs=[InputSpec(name=n, type=t) for n, t in inputs])


def study(space, fixed=None, max_passes=2000):
    return StudyRequest(study_id="s", strategy_file="x", symbol="EURUSD", timeframe="H1",
                        from_date=dt.date(2022, 1, 3), to_date=dt.date(2023, 1, 2), param_space=space,
                        fixed=fixed or {}, tester=TESTER, max_passes_per_job=max_passes)


ST = strategy(("InpFast", "int"), ("InpSlow", "int"), ("InpLots", "double"))


def test_grid_matches_the_hardware_run():
    g = build_grid(study({"InpFast": {"start": 5, "step": 5, "stop": 15},
                          "InpSlow": {"start": 20, "step": 10, "stop": 50}}, {"InpLots": 0.01}), ST)
    assert g.total == 12 and len(g.chunks) == 1
    assert g.axes[0].values == (5, 10, 15) and g.axes[1].values == (20, 30, 40, 50)
    assert g.fixed == {"InpLots": "0.01"}
    # every parameter set reported by the hardware frames is in the grid (verification/p3 O1)
    with open(FIX / "verify_frames_O1.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    got = {match_combo(g.chunks[0], {"InpFast": r["fast"], "InpSlow": r["slow"]}) for r in rows}
    assert got == set(g.chunks[0].combos())


def test_double_axis_has_no_float_drift_and_matches_mt5_text():
    st = strategy(("InpLots", "double"))
    g = build_grid(study({"InpLots": {"start": 0.1, "step": 0.1, "stop": 0.7}}), st)
    assert g.axes[0].values == ("0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7")
    assert match_combo(g.chunks[0], {"InpLots": "0.30000000"}) == ("0.3",)
    assert match_combo(g.chunks[0], {"InpLots": "0.35"}) is None


def test_chunks_split_the_first_axis():
    g = build_grid(study({"InpFast": {"start": 1, "step": 1, "stop": 10},
                          "InpSlow": {"start": 1, "step": 1, "stop": 3}}, {"InpLots": 0.01}, max_passes=7), ST)
    assert [c.passes for c in g.chunks] == [6, 6, 6, 6, 6]
    assert [c.axes[0].values for c in g.chunks][-1] == (9, 10)
    assert sum(c.passes for c in g.chunks) == g.total == 30


@pytest.mark.parametrize(("space", "fixed", "msg"), [
    ({"InpFast": {"start": 1, "step": 2, "stop": 4}}, {"InpSlow": 1, "InpLots": 0.1}, "not a multiple"),
    ({"InpFast": {"start": 1, "step": 0.5, "stop": 2}}, {"InpSlow": 1, "InpLots": 0.1}, "integer start and step"),
    ({"InpFast": {"start": 1, "step": 1, "stop": 2}}, {"InpLots": 0.1}, "neither optimized nor fixed"),
    ({"InpNope": {"start": 1, "step": 1, "stop": 2}}, {"InpFast": 1, "InpSlow": 1, "InpLots": 0.1}, "not declared"),
    ({"InpFast": {"start": 1, "step": 1, "stop": 2}, "InpSlow": {"start": 1, "step": 1, "stop": 5}},
     {"InpLots": 0.1, "RL_JobId": "x"}, "not declared"),
])
def test_grid_rejects_bad_specs(space, fixed, msg):
    with pytest.raises(GridError, match=msg):
        build_grid(study(space, fixed), ST)


def test_one_value_of_the_first_axis_must_fit_in_a_job():
    with pytest.raises(GridError, match="max_passes_per_job"):
        build_grid(study({"InpFast": {"start": 1, "step": 1, "stop": 2},
                          "InpSlow": {"start": 1, "step": 1, "stop": 10}}, {"InpLots": 0.1}, max_passes=5), ST)


def test_optimization_set_and_ini():
    g = build_grid(study({"InpFast": {"start": 5, "step": 5, "stop": 15},
                          "InpSlow": {"start": 20, "step": 10, "stop": 50}}, {"InpLots": 0.01}), ST)
    text = ini_builder.build_opt_set_text(ST.inputs, g.fixed, g.chunks[0].axes, "jb_1")
    assert text.split("\r\n")[:4] == ["InpFast=5||5||5||15||Y", "InpSlow=20||20||10||50||Y",
                                      "InpLots=0.01||0.01||0.1||0.01||N", "RL_JobId=jb_1"]
    from robustlab.core.models import TesterSettings
    ini = ini_builder.build_ini_text(expert="RobustLab\\S", set_filename="a.set", symbol="EURUSD", timeframe="H1",
                                     from_date=dt.date(2022, 1, 3), to_date=dt.date(2023, 1, 2),
                                     tester=TesterSettings(**TESTER), job_id="jb_1", optimization=True)
    assert "Optimization=1\r\n" in ini and "OptimizationCriterion" not in ini
    assert "UseCloud=0" in ini and "UseRemote=0" in ini


def test_tester_log_lines_from_hardware():
    # lines exactly as in verification/p3 (O1 and O2)
    o1 = ("Tester\tcomplete optimization started\r\nTester\toptimization finished, total passes 12\r\n"
          "Statistics\tlocal 12 tasks (100%), remote 0 tasks (0%), cloud 0 tasks (0%)\r\n")
    f = read_tester_log(o1)
    assert (f.started, f.finished_passes, f.cache_hit, f.local_tasks, f.remote_tasks, f.cloud_tasks) == \
        (True, 12, False, 12, 0, 0)
    o2 = ("Tester\tcomplete optimization started\r\nTester\toptimization already processed, total passes 12\r\n"
          "Tester\treading of 12 result records from cache...\r\n")
    f = read_tester_log(o2)
    assert f.cache_hit and f.finished_passes is None and f.local_tasks is None


def test_hardware_xml_is_read_by_column_names():
    rows = read_opt_report((FIX / "opt_report_O1.xml").read_bytes(), ["InpFast", "InpSlow"])
    assert len(rows) == 12
    top = rows[0]  # rows are sorted by Result, not by pass
    assert (top["Pass"], top["Profit"], top["Trades"], top["InpFast"], top["InpSlow"]) == ("4", "38.00", "49", "10", "30")
    assert sorted(int(r["Pass"]) for r in rows) == list(range(12))
    with pytest.raises(XmlReportError, match="InpNope"):
        read_opt_report((FIX / "opt_report_O1.xml").read_bytes(), ["InpNope"])
    with pytest.raises(XmlReportError, match="XML_UNREADABLE"):
        read_opt_report(b"<not xml", [])


def test_pass_metrics_from_hardware_pass_values():
    # pass 4 of verification/p3 O1 (identical to the single test S1)
    stats = {"initial_deposit": 10000, "profit": 38.0, "gross_profit": 136.71, "gross_loss": -98.71, "trades": 49,
             "deals": 98, "balance_dd": 30.14, "equity_dd": 38.93, "profit_factor": 1.38, "expected_payoff": 0.78,
             "recovery_factor": 0.98, "sharpe_ratio": 1.57, "profit_trades": 20, "loss_trades": 29,
             "max_conwins": 25.0, "max_conprofit_trades": 3, "max_conlosses": -20.0, "max_conloss_trades": 5}
    tracking = {"equity_peak": 10060.0, "equity_min": 9970.0, "equity_max_dd": 38.93, "equity_max_dd_pct": 0.39,
                "max_floating_loss": -10.0, "max_positions": 1, "max_lots": 0.01, "min_margin_level": 5000.0,
                "stop_out_deals": 0}
    daily = [{"equity_close": "10010"}, {"equity_close": "9990"}, {"equity_close": "10038"}]
    m, _ = compute_pass(stats, tracking, daily, 10000)
    assert m["net_profit"] == 38.0 and m["trades"] == 49 and m["final_balance"] == 10038.0
    assert round(m["profit_factor"], 6) == round(136.71 / 98.71, 6)
    assert m["max_consec_wins"] == 3 and m["max_consec_wins_amount"] == 25.0
    assert m["recovery_factor"] == pytest.approx(38.0 / 38.93)
    assert m["days"] == 3 and m["bankrupt"] is False and "daily_sharpe" in m
