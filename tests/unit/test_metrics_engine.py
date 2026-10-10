"""MetricEngine m1 against the hardware fixture and the matching MT5 report values (P2_PLAN §1.1)."""

import csv

import pytest

from robustlab.metrics import compare, engine
from tests.telemetry_factory import FIXTURE_DEALS

DEALS = list(csv.DictReader(open(FIXTURE_DEALS, encoding="utf-8")))
# Values from the MT5 report of the same run (R1, 2026-10-09)
MT5 = {"profit": 9.69, "gross_profit": 32.01, "gross_loss": -22.32, "trades": 32.0, "profit_trades": 16.0,
       "loss_trades": 16.0, "profit_factor": 1.43, "expected_payoff": 0.30, "balance_dd": 7.43,
       "equity_dd": 5.07, "recovery_factor": 1.91, "sharpe_ratio": 14.51, "max_conwins": 6.0,
       "max_conlosses": 3.0, "max_conprofit_trades": 5.14, "max_conloss_trades": -1.23}
TRACKED = {"equity_peak": 10013.0, "equity_max_dd": 5.07, "equity_max_dd_pct": 0.05, "max_floating_loss": -4.0,
           "max_positions": 2, "max_lots": 0.02, "min_margin_level": 92255.3, "stop_out_deals": 0, "equity_min": 9995.0}


def test_trade_metrics_match_mt5_report():
    r = engine.compute(DEALS, initial_deposit=10000.0, tracked=TRACKED)
    rows = {c["name"]: c for c in compare.compare(r.metrics, MT5)}
    assert all(c["status"] == "MATCH" for c in rows.values() if c["name"] != "daily_sharpe"), [
        c for c in rows.values() if c["status"] != "MATCH"]
    assert rows["daily_sharpe"]["status"] == "NOT_COMPARABLE"
    assert r.metrics["fixed_lot"] is True and r.metrics["final_balance"] == 10009.69


def test_recovery_factor_uses_equity_drawdown_like_mt5():
    r = engine.compute(DEALS, initial_deposit=10000.0, tracked=TRACKED)
    assert round(r.metrics["recovery_factor"], 2) == 1.91  # 9.69 / 5.07, not 9.69 / 7.43


def test_v1_telemetry_has_trade_metrics_and_reasoned_nulls():
    r = engine.compute(DEALS, initial_deposit=10000.0)
    assert r.metrics["trades"] == 32 and r.metrics["recovery_factor"] is None
    assert "v2" in r.unavailable["recovery_factor"] and "v2" in r.unavailable["daily_sharpe"]
    rows = {c["name"]: c["status"] for c in compare.compare(r.metrics, MT5)}
    assert rows["equity_max_dd"] == "OURS_MISSING" and rows["net_profit"] == "MATCH"


def test_daily_series_metrics():
    daily = [{"equity_close": v} for v in ("10002", "9998", "10006", "10009.69")]
    r = engine.compute(DEALS, initial_deposit=10000.0, daily=daily, tracked=TRACKED)
    rets = [0.0002, -0.0004, 0.0008, 0.000369]
    assert r.metrics["days"] == 4
    assert r.metrics["daily_mean_return"] == pytest.approx(sum(rets) / 4)
    assert r.metrics["daily_sharpe"] is not None and r.metrics["daily_kurtosis"] > 0


def _deal(ticket, t, entry, profit, typ=0, vol="0.01"):
    return {"ticket": str(ticket), "time": t, "time_msc": str(ticket), "type": str(typ), "entry": str(entry),
            "volume": vol, "price": "1", "profit": profit, "commission": "0", "swap": "0", "fee": "0"}


def test_edge_cases_no_trades_all_wins():
    none = engine.compute([_deal(1, "2023.01.02 00:00:00", 0, "10000", typ=2)], initial_deposit=10000.0)
    assert none.metrics["trades"] == 0 and none.metrics["profit_factor"] is None
    assert none.unavailable["win_rate"] == "no closed trades"
    wins = engine.compute([_deal(1, "2023.01.02 00:00:00", 0, "0"), _deal(2, "2023.01.02 01:00:00", 1, "5")],
                          initial_deposit=100.0)
    assert wins.metrics["profit_factor"] is None and wins.unavailable["profit_factor"] == "no losing trades"
    assert wins.metrics["max_consec_wins"] == 1 and wins.metrics["balance_max_dd"] == 0


def test_periods_split_by_year_and_month():
    ds = [_deal(1, "2023.12.31 10:00:00", 1, "-3"), _deal(2, "2024.01.02 10:00:00", 1, "5")]
    r = engine.compute(ds, initial_deposit=100.0)
    years = {p["bucket_key"]: p for p in r.periods if p["bucket_type"] == "YEAR"}
    assert years["2023"]["net_profit"] == -3 and years["2024"]["net_profit"] == 5
    assert {p["bucket_key"] for p in r.periods if p["bucket_type"] == "MONTH"} == {"2023-12", "2024-01"}


def test_mixed_lots_are_flagged():
    ds = [_deal(1, "2023.01.02 00:00:00", 0, "0", vol="0.01"), _deal(2, "2023.01.02 01:00:00", 1, "1", vol="0.02")]
    assert engine.compute(ds, initial_deposit=100.0).metrics["fixed_lot"] is False


def test_longest_runs_lists_every_tie():
    ds = [_deal(1, "2023.01.02 00:00:00", 1, "1"), _deal(2, "2023.01.02 01:00:00", 1, "2"),
          _deal(3, "2023.01.02 02:00:00", 1, "-1"),
          _deal(4, "2023.01.02 03:00:00", 1, "5"), _deal(5, "2023.01.02 04:00:00", 1, "1")]
    r = engine.longest_runs(ds)
    assert [x["amount"] for x in r["wins"]] == [3.0, 6.0]
    assert r["most_wins_amount"][0]["amount"] == 6.0 and r["losses"][0]["length"] == 1
