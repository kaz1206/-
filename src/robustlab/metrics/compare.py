"""Compare MetricEngine values with MT5 TesterStatistics (P2_PLAN §3).

MT5 values arrive rounded to 2 decimals (Telemetry prints them that way), so our values
are rounded the same way before comparison.
"""

from __future__ import annotations

from typing import Any

TOLERANCE = 0.0100001
# H3 (C39): MT5's equity drawdown sampling is unknown; our tick-level value is kept for research
# and a relative difference within 1% is APPROX_MATCH (observed: 0.26%).
APPROX_RELATIVE = {"equity_max_dd": 0.01}

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
    # H1 (C37), confirmed on hardware: STAT_MAX_CONWINS / STAT_MAX_CONLOSSES are the AMOUNTS of the
    # longest runs and STAT_MAX_CONPROFIT_TRADES / STAT_MAX_CONLOSS_TRADES their COUNTS.
    "max_consec_wins": "max_conprofit_trades",
    "max_consec_losses": "max_conloss_trades",
    "max_consec_wins_amount": "max_conwins",
    "max_consec_losses_amount": "max_conlosses",
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
            rel = APPROX_RELATIVE.get(ours_name)
            if abs(diff) <= TOLERANCE:
                status = "MATCH"
            elif rel is not None and float(theirs) != 0 and abs(diff) / abs(float(theirs)) <= rel:
                status = "APPROX_MATCH"
            else:
                status = "MISMATCH"
        rows.append({"name": ours_name, "ours": ours, "mt5": theirs, "diff": diff, "status": status})
    for ours_name, mt5_name in NOT_COMPARABLE.items():
        rows.append({"name": ours_name, "ours": metrics.get(ours_name), "mt5": mt5.get(mt5_name),
                     "diff": None, "status": "NOT_COMPARABLE"})
    return rows
