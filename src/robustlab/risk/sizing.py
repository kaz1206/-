"""Lot sizing of P9 (P9_PLAN §3.2-§3.3, ARCHITECTURE §12.3-§12.4, C10, C12).

lot_raw    = min(lot_safe, lot_kelly, lot_cap, lot_margin)          binding constraint recorded
lot_target = lot_raw * confidence_weight * ramp_up
lot        = volume_min + floor((lot_target - volume_min) / step) * step, capped (also downwards) at volume_max

Feasibility (principle 4 of 2026-10-11): the lot is only ever rounded DOWN. lot_target < volume_min ->
INFEASIBLE_MIN_LOT (lot 0); if the margin budget is what keeps it below the minimum -> MARGIN_BUDGET_EXCEEDED.
There is no option, argument or setting that rounds up or relaxes a limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Any

import numpy as np
from arch.bootstrap import StationaryBootstrap

SIZING_VERSION = "size1"
FLOAT_TOLERANCE = Decimal("1e-12")  # lots; absorbs binary noise such as 0.07 -> 0.069999999999999 (never more)

ALLOCATED = "ALLOCATED"
INFEASIBLE_MIN_LOT = "INFEASIBLE_MIN_LOT"
MARGIN_BUDGET_EXCEEDED = "MARGIN_BUDGET_EXCEEDED"
NO_ALLOCATION_MU_LCB = "NO_ALLOCATION_MU_LCB"


def floor_to_step(target: float, volume_min: float, step: float, volume_max: float) -> Decimal:
    """Largest volume_min + n*step <= target (n >= 0), at most volume_max; Decimal(0) when target < volume_min."""
    t = Decimal(repr(float(target))) + FLOAT_TOLERANCE
    vmin, st, vmax = Decimal(repr(volume_min)), Decimal(repr(step)), Decimal(repr(volume_max))
    if st <= 0 or vmin <= 0:
        raise ValueError("volume_min and volume_step must be > 0")
    if t < vmin:
        return Decimal(0)
    n = ((min(t, vmax) - vmin) / st).to_integral_value(rounding=ROUND_FLOOR)
    return vmin + n * st


def mu_lcb(h_x: np.ndarray, seg_lens: tuple[int, ...], block: float, reps: int, q: float,
           rng: np.random.Generator) -> float:
    """Lower q-quantile of the pooled daily mean (per lot) under the per-period stationary bootstrap."""
    ends = np.cumsum(seg_lens)
    gens = [StationaryBootstrap(block, h_x[e - n:e], seed=rng).bootstrap(reps)
            for e, n in zip(ends, seg_lens, strict=True)]
    means = [float(np.concatenate([d[0][0] for d in draws]).mean()) for draws in zip(*gens, strict=True)]
    return float(np.quantile(means, q))


@dataclass
class Sizing:
    outcome: str
    lot: float
    lot_target: float
    lot_raw: float
    binding: str
    limits: dict[str, float]
    notes: dict[str, Any] = field(default_factory=dict)


def size(*, equity: float, lot_safe: float, safe_binding: str, mu: float, var: float, trade_loss_q99: float,
         margin_per_lot: float, max_positions: float, volume_min: float, volume_step: float, volume_max: float,
         kelly_fraction: float, cap_per_trade: float, margin_budget: float, confidence_weight: float,
         ramp_up: float) -> Sizing:
    """All money per lot in account currency; equity in account currency; mu = mu_LCB per lot per day."""
    lot_kelly = kelly_fraction * max(0.0, mu) / var * equity if var > 0 else 0.0
    lot_cap = cap_per_trade * equity / trade_loss_q99 if trade_loss_q99 > 0 else float("inf")
    lot_margin = (margin_budget * equity / (margin_per_lot * max(1.0, max_positions))
                  if margin_per_lot > 0 else float("inf"))
    limits = {"safe": lot_safe, "kelly": lot_kelly, "cap": lot_cap, "margin": lot_margin}
    binding = min(limits, key=limits.get)
    lot_raw = limits[binding]
    lot_target = lot_raw * confidence_weight * ramp_up
    notes: dict[str, Any] = {"safe_binding": safe_binding}
    if mu <= 0:
        return Sizing(NO_ALLOCATION_MU_LCB, 0.0, 0.0, 0.0, "kelly", limits, notes)
    lot = floor_to_step(lot_target, volume_min, volume_step, volume_max)
    if lot == 0:
        notes["equity_needed_for_min_lot"] = equity * volume_min / lot_target if lot_target > 0 else None
        outcome = MARGIN_BUDGET_EXCEEDED if binding == "margin" else INFEASIBLE_MIN_LOT
        return Sizing(outcome, 0.0, lot_target, lot_raw, binding, limits, notes)
    if float(lot) > lot_target + float(FLOAT_TOLERANCE):  # cannot happen; refuse rather than round up
        raise AssertionError(f"lot {lot} above target {lot_target}")
    return Sizing(ALLOCATED, float(lot), lot_target, lot_raw, binding, limits, notes)
