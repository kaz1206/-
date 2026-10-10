"""Compare MetricEngine values with MT5 TesterStatistics (P2_PLAN §3).

MT5 values arrive rounded to 2 decimals (Telemetry prints them that way), so our values
are rounded the same way before comparison.
"""

from __future__ import annotations

from typing import Any

TOLERANCE = 0.0100001

# our metric -> MT5 statistic name in the telemetry stats file
REQUIRED_MATCH = {
    "net_profit": "profit",
    "gross_profit": "gross_profit",
    "gross_loss": "gross_loss",
    "trades": "trades",
    "win_trades": "profit_trades",
    "loss_trades": "loss_trades",
    "profit_factor": "profit_factor",
    "expected_payoff": "expected_payoff",
    "balance_max_dd": "balance_dd",
    "equity_max_dd": "equity_dd",
    "recovery_factor": "recovery_factor",
    "max_consec_wins": "max_conwins",
    "max_consec_losses": "max_conlosses",
    # amount of the LONGEST run (report "max consecutive wins ($)"); STAT_CONPROFITMAX is the most
    # profitable run instead, a different statistic. Names are confirmed on hardware (P2_PLAN V2-1/V2-3).
    "max_consec_wins_amount": "max_conprofit_trades",
    "max_consec_losses_amount": "max_conloss_trades",
}
NOT_COMPARABLE = {"daily_sharpe": "sharpe_ratio"}  # MT5 definition unknown (P2_PLAN §1.1)


def compare(metrics: dict[str, Any], mt5: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for ours_name, mt5_name in REQUIRED_MATCH.items():
        ours, theirs = metrics.get(ours_name), mt5.get(mt5_name)
        if theirs is None:
            status, diff = "MT5_MISSING", None
        elif ours is None:
            status, diff = "OURS_MISSING", None
        else:
            diff = round(float(ours), 2) - float(theirs)
            status = "MATCH" if abs(diff) <= TOLERANCE else "MISMATCH"
        rows.append({"name": ours_name, "ours": ours, "mt5": theirs, "diff": diff, "status": status})
    for ours_name, mt5_name in NOT_COMPARABLE.items():
        rows.append({"name": ours_name, "ours": metrics.get(ours_name), "mt5": mt5.get(mt5_name),
                     "diff": None, "status": "NOT_COMPARABLE"})
    return rows
