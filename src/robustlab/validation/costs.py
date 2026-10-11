"""Cost stress and profit concentration on single-test deals (P8_PLAN §4.3, C60, C72).

- The extra cost is a PRICE WIDTH per round trip, fixed per symbol in configs/cost_stress.yaml ("pip" is not
  used: it means different things for FX and XAUUSD). Amount per closing deal =
  width / tick_size * tick_value * volume, with tick_size / tick_value from the job's telemetry (the value at
  the end of the test; an approximation when the profit currency differs from the account currency).
- It is charged once per CLOSING deal (entry OUT / INOUT / OUT_BY), in proportion to that deal's volume, ON TOP
  of the tester's result, which already contains the spread, commission, swap and fee (nothing is charged twice).
- It is used for COST_FRAGILE only, never for the evidence p-value.
"""

from __future__ import annotations

from typing import Any

from robustlab.metrics.engine import CLOSING_ENTRIES, DEAL_BUY, DEAL_SELL

TOP_N = 5


def closing_deals(deals: list[dict[str, str]]) -> list[dict[str, str]]:
    return [d for d in deals if int(d["type"]) in (DEAL_BUY, DEAL_SELL) and int(d["entry"]) in CLOSING_ENTRIES]


def deal_pnl(d: dict[str, str]) -> float:
    return float(d["profit"]) + float(d["commission"]) + float(d["swap"]) + float(d["fee"])


def extra_cost(deals: list[dict[str, str]], width: float, tick_size: float, tick_value: float,
               factor: float = 1.0) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    per_lot = width * factor / tick_size * tick_value
    return sum(per_lot * float(d["volume"]) for d in closing_deals(deals))


def concentration(deals: list[dict[str, str]], top: int = TOP_N) -> dict[str, Any]:
    pnls = sorted((deal_pnl(d) for d in closing_deals(deals)), reverse=True)
    return {"closing_deals": len(pnls), "top": top, "top_sum": sum(pnls[:top]), "rest_sum": sum(pnls[top:]),
            "gross_profit": sum(p for p in pnls if p > 0)}
