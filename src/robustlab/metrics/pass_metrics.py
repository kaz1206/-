"""Metrics for one optimization pass (P3_PLAN §3.4, docs/metrics_definitions.md "p1").

Optimization passes have no deal list (only a frame per pass), so:
- trade metrics come from TesterStatistics, limited to the ones whose definitions were shown to
  equal our deal-based definitions on hardware in P2 (H1, C37) - an explicit exception to
  "compute research values ourselves";
- equity metrics come from the EA tracking values (as in m2);
- daily-return metrics are computed by us from the daily equity series (same code as m2).
"""

from __future__ import annotations

from typing import Any

from robustlab.metrics.engine import daily_metrics, finalize

PASS_METRIC_DEF_VERSION = "p1"


def compute_pass(stats: dict[str, float], tracking: dict[str, Any], daily: list[dict[str, str]],
                 initial_deposit: float) -> tuple[dict[str, Any], dict[str, str]]:
    m: dict[str, Any] = {}
    na: dict[str, str] = {}
    trades = int(stats["trades"])
    m["net_profit"] = round(stats["profit"], 2)
    m["trades"] = trades
    m["win_trades"] = int(stats["profit_trades"])
    m["loss_trades"] = int(stats["loss_trades"])
    m["gross_profit"] = round(stats["gross_profit"], 2)
    m["gross_loss"] = round(stats["gross_loss"], 2)
    if trades:
        m["win_rate"] = m["win_trades"] / trades
        m["expected_payoff"] = stats["profit"] / trades
    else:
        na["win_rate"] = na["expected_payoff"] = "no closed trades"
    if m["gross_loss"] < 0:
        m["profit_factor"] = m["gross_profit"] / -m["gross_loss"]
    else:
        na["profit_factor"] = "no losing trades"
    # P2 (H1): STAT_MAX_CONWINS / CONLOSSES are amounts, *_TRADES are counts
    m["max_consec_wins"] = int(stats["max_conprofit_trades"])
    m["max_consec_wins_amount"] = round(stats["max_conwins"], 2)
    m["max_consec_losses"] = int(stats["max_conloss_trades"])
    m["max_consec_losses_amount"] = round(stats["max_conlosses"], 2)
    m["balance_max_dd"] = round(stats["balance_dd"], 2)
    m["final_balance"] = round(initial_deposit + stats["profit"], 2)

    for k in ("equity_peak", "equity_max_dd_pct", "max_floating_loss", "max_positions", "max_lots",
              "min_margin_level", "stop_out_deals"):
        m[k] = tracking[k]
    m["equity_max_dd"] = round(float(tracking["equity_max_dd"]), 2)
    m["bankrupt"] = float(tracking["equity_min"]) <= 0
    if m["equity_max_dd"] > 0:
        m["recovery_factor"] = m["net_profit"] / m["equity_max_dd"]
    else:
        na["recovery_factor"] = "no equity drawdown"

    daily_metrics(daily, initial_deposit, m, na)
    finalize(m, na)
    return m, na
