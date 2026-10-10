"""Sub-period (calendar year) consistency from the daily equity of one pass (ARCHITECTURE §9.2, P5_PLAN §3).

Daily return r_t = (E_t - E_{t-1}) / D with E_0 = D (the same definition as the PSR input, C53).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def yearly(dates: list[str], equity: list[float], deposit: float) -> dict[str, Any]:
    """dates as 'YYYY.MM.DD' in order, equity = end-of-day equity on those dates."""
    if not dates:
        return {"years": 0, "reason": "no daily data"}
    eq = np.array([deposit, *equity], dtype=float)
    r = np.diff(eq) / deposit
    by_year: dict[str, list[float]] = {}
    for d, x in zip(dates, r, strict=True):
        by_year.setdefault(d[:4], []).append(float(x))
    per_year = {}
    for y, xs in sorted(by_year.items()):
        a = np.array(xs)
        sd = float(a.std(ddof=1)) if a.size >= 2 else 0.0
        per_year[y] = {"return": float(a.sum()), "days": int(a.size),
                       "sharpe": float(a.mean() / sd) if sd > 0 else None}
    rets = [v["return"] for v in per_year.values()]
    sharpes = [v["sharpe"] for v in per_year.values() if v["sharpe"] is not None and math.isfinite(v["sharpe"])]
    return {
        "years": len(per_year),
        "per_year": per_year,
        "positive_year_share": sum(x > 0 for x in rets) / len(rets),
        "worst_year_return": min(rets),
        "year_sharpe_std": float(np.std(sharpes, ddof=1)) if len(sharpes) >= 2 else None,
        "reason": None if len(per_year) >= 2 else "fewer than 2 calendar years: consistency is weak evidence",
    }
