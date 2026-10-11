"""Block-bootstrap intervals for one candidate's daily returns (P6_PLAN §3-§4, C65-C66).

- Main: arch StationaryBootstrap (Politis-Romano), mean block length from optimal_block_length
  (Politis-White 2004 with the Patton-Politis-White 2009 correction), column "stationary", at least 1 [S-ARCH-800].
- Sensitivity: CircularBlockBootstrap with the "circular" block length.
- Percentile intervals, two-sided 90%: np.percentile (linear) of the replicates, as arch's conf_int(method=
  "percentile") does.
- Statistics: mean daily return, daily Sharpe (ddof=1), and the resampled max drawdown distribution (5/50/95%)
  of the end-of-day equity path E*_t = D + D * cumsum(r*) with D included as the first peak (§4.3).
  The max drawdown is a path statistic: we report a distribution, not a confidence interval.
- Intervals describe the sampling error of this period only; they are not forecasts. Block bootstraps keep
  dependence up to about the block length; longer regimes and unseen events are not reflected.
"""

from __future__ import annotations

import math
from typing import Any

import arch
import numpy as np
from arch.bootstrap import CircularBlockBootstrap, StationaryBootstrap, optimal_block_length

BOOTSTRAP_VERSION = "bs1"
MIN_DAYS = 30
CI_SIZE = 0.90
DD_QUANTILES = (5.0, 50.0, 95.0)
MAX_ZERO_STD_SHARE = 0.01
ARCH_VERSION = arch.__version__


def block_lengths(x: np.ndarray) -> dict[str, float]:
    t = optimal_block_length(np.asarray(x, dtype=float))
    return {"stationary": max(1.0, float(t["stationary"].iloc[0])), "circular": max(1.0, float(t["circular"].iloc[0]))}


def max_drawdown(r: np.ndarray, deposit: float) -> tuple[float, float, bool]:
    """(max DD in account currency, DD % of the peak at that point, path touched E <= 0). §4.3 steps 2-5."""
    eq = deposit + deposit * np.cumsum(r)
    path = np.concatenate([[deposit], eq])
    peak = np.maximum.accumulate(path)
    dd = peak - path
    i = int(np.argmax(dd))
    pct = float(dd[i] / peak[i] * 100) if peak[i] > 0 else math.nan
    return float(dd[i]), pct, bool(np.any(eq <= 0))


def _stats(deposit: float):
    def f(x: np.ndarray) -> np.ndarray:
        sd = float(x.std(ddof=1))
        dd, pct, broke = max_drawdown(x, deposit)
        return np.array([float(x.mean()), float(x.mean()) / sd if sd > 0 else np.nan, dd, pct, float(broke)])
    return f


def _interval(vals: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(vals, [100 * (1 - CI_SIZE) / 2, 100 * (1 + CI_SIZE) / 2])
    return float(lo), float(hi)


def _summarize(res: np.ndarray, reps: int) -> dict[str, Any]:
    mean, sharpe, dd, ddp, broke = res.T
    zero = int(np.count_nonzero(np.isnan(sharpe)))
    out: dict[str, Any] = {"mean_ci": _interval(mean)}
    if zero / reps > MAX_ZERO_STD_SHARE:
        out["sharpe_ci"], out["sharpe_ci_reason"] = None, (
            f"unstable: {zero} of {reps} resamples had zero variance (> {MAX_ZERO_STD_SHARE:.0%})")
        out["sharpe_se"] = None
    else:
        s = sharpe[~np.isnan(sharpe)]
        out["sharpe_ci"], out["sharpe_ci_reason"] = _interval(s), None
        out["sharpe_se"] = float(s.std(ddof=1))
    out["sharpe_zero_variance_resamples"] = zero
    out["max_dd_distribution"] = {f"p{int(q)}": float(np.percentile(dd, q)) for q in DD_QUANTILES}
    finite_pct = ddp[~np.isnan(ddp)]
    out["max_dd_pct_distribution"] = ({f"p{int(q)}": float(np.percentile(finite_pct, q)) for q in DD_QUANTILES}
                                      if finite_pct.size else None)
    out["resampled_bankrupt_share"] = float(broke.mean())
    return out


def evaluate(returns: np.ndarray, deposit: float, reps: int, seed: int) -> dict[str, Any]:
    """Stationary bootstrap (main) and circular block bootstrap (sensitivity) for one return series."""
    x = np.asarray(returns, dtype=float)
    base = {"method": "percentile", "size": CI_SIZE, "reps": reps, "seed": seed, "arch_version": ARCH_VERSION,
            "version": BOOTSTRAP_VERSION, "n": int(len(x))}
    if len(x) < MIN_DAYS:
        return base | {"status": "NOT_COMPUTED", "reason": f"fewer than {MIN_DAYS} daily returns ({len(x)})"}
    if not np.all(np.isfinite(x)):
        return base | {"status": "NOT_COMPUTED", "reason": "daily returns contain NaN or infinite values"}
    if float(x.std()) == 0:
        return base | {"status": "NOT_COMPUTED", "reason": "zero variance (no trades or flat equity)"}
    bl = block_lengths(x)
    f = _stats(deposit)
    sd = float(x.std(ddof=1))
    dd, ddp, _ = max_drawdown(x, deposit)
    point = {"mean": float(x.mean()), "mean_amount": float(x.mean() * deposit), "sharpe": float(x.mean()) / sd,
             "max_dd_daily_close": dd, "max_dd_daily_close_pct": ddp}
    rng = np.random.Generator(np.random.PCG64(seed))
    sb = StationaryBootstrap(bl["stationary"], x, seed=rng)
    main = _summarize(sb.apply(f, reps), reps)
    cb = CircularBlockBootstrap(max(1, int(round(bl["circular"]))), x, seed=rng)
    sens = _summarize(cb.apply(f, reps), reps)
    main["mean_amount_ci"] = tuple(v * deposit for v in main["mean_ci"])
    return base | {"status": "OK", "reason": None, "block_length": bl, "point": point,
                   "stationary": main, "circular_sensitivity": sens,
                   "circular_block_length_used": max(1, int(round(bl["circular"]))),
                   "sensitivity_note": _compare(main, sens)}


def stationary_sharpe_se(x: np.ndarray, reps: int, rng: np.random.Generator) -> float | None:
    """Sharpe standard error from the stationary bootstrap only (same estimator as evaluate(); used by the
    Monte Carlo validation, where the intervals and the circular sensitivity are not needed)."""
    x = np.asarray(x, dtype=float)
    bs = StationaryBootstrap(block_lengths(x)["stationary"], x, seed=rng)
    s = bs.apply(lambda z: np.array([z.mean() / z.std(ddof=1)]), reps)[:, 0]
    s = s[np.isfinite(s)]
    return float(s.std(ddof=1)) if s.size >= 2 else None


def _compare(main: dict[str, Any], sens: dict[str, Any]) -> str | None:
    """A note when the circular-block interval differs much from the stationary one (width ratio outside 2/3..3/2)."""
    notes = []
    for k in ("mean_ci", "sharpe_ci"):
        a, b = main.get(k), sens.get(k)
        if a is None or b is None:
            continue
        wa, wb = a[1] - a[0], b[1] - b[0]
        if wa > 0 and not (2 / 3 <= wb / wa <= 1.5):
            notes.append(f"{k}: circular width / stationary width = {wb / wa:.2f}")
    return "; ".join(notes) or None
