"""Deflated Sharpe Ratio and serial-dependence corrections (P6_PLAN §1.1, §2, C61-C64).

    SR0 = sqrt(V) * ((1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N e)))      [S-DSR]
    DSR = PSR(SR0)

V is the sample variance of the per-period Sharpe estimates across trials (not annualized), N the number
of trials. With N = 1 there is no selection: SR0 = 0 and DSR = PSR(0). With V = 0, SR0 = 0 (reason recorded).

Serial-dependence corrections (C63, 独自拡張: the paper assumes independent returns). Each replaces the
number of observations n in PSR by an effective size n*, or the Sharpe standard error by a bootstrap one:
- AR(1):        n* = n (1 - rho1) / (1 + rho1)                     (only when rho1 > 0)
- Newey-West:   n* = n / f,  f = 1 + 2 sum_{k<=L} (1 - k/(L+1)) rho_k,  L = floor(4 (n/100)^(2/9))  (only f > 1)
- bootstrap SE: DSR_boot = Phi((SR - SR0) / SE_boot), SE_boot from the stationary-bootstrap replicates
The conservative DSR is the minimum of the variants that could be computed.

IMPORTANT (C62, approval condition 1-2): none of these values guarantees a 5% chance-pass rate. Under some
dependence forms (AR(2), MA(5)) every variant exceeded the pre-registered 7% bound in the P6 simulations.
DSR values are for warnings and ranking only and never decide pass/fail on their own. KNOWN_LIMITATIONS is
copied into every result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import norm

from robustlab.stats.psr import psr

DSR_VERSION = "dsr1"
EULER_GAMMA = 0.5772156649015329
ACF_LAGS = 5
SERIAL_DEPENDENCE_RATIO = 1.5  # n / n* at or above this (n* <= 2/3 n) sets SERIAL_DEPENDENCE_HIGH (独自拡張)
USAGE = "WARNING_AND_RANKING_ONLY"  # C62: never a pass/fail criterion on its own
KNOWN_LIMITATIONS = (
    "DSR assumes independent daily returns; the serial-dependence corrections reduce but do not remove the "
    "excess chance-pass rate.",
    "P6 validation (100 null strategies x 500 days, best selected, 2,000 repetitions): no variant met the "
    "pre-registered bound (95% upper limit <= 7%) under AR(2) 0.3/0.3 (conservative DSR 6.3%, upper 7.5%); the "
    "uncorrected DSR exceeds it whenever returns are autocorrelated (11-22%); Newey-West alone fails under MA(5). "
    "Other dependence forms may be worse. See docs/research/p6_validation/RESULTS.md.",
    "Corrections cost power (independent 93.5% -> AR(1) 0.2: 76%, AR(1) 0.4: 46%).",
    "A single huge winning day raises PSR/DSR through the skewness term (a property of the formula).",
    "Not a guarantee of safety: the final checks against chance passes are P8 (new period) and P10 "
    "(null-hypothesis test of the whole pipeline).",
)


@dataclass(frozen=True)
class Sr0:
    value: float | None
    reason: str | None = None


def sr0(n_trials: int | None, variance: float | None) -> Sr0:
    if n_trials is None or n_trials < 1:
        return Sr0(None, "number of trials missing")
    if n_trials == 1:
        return Sr0(0.0, "N = 1: no selection, SR0 = 0")
    if variance is None or not math.isfinite(variance) or variance < 0:
        return Sr0(None, "variance of the trial Sharpe ratios missing")
    if variance == 0:
        return Sr0(0.0, "V[SR] = 0: all trial Sharpe ratios equal, SR0 = 0")
    z = (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials) + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * math.e))
    return Sr0(float(math.sqrt(variance) * z))


def acf(x: np.ndarray, k: int) -> float | None:
    x = np.asarray(x, dtype=float)
    if k >= len(x):
        return None
    d = x - x.mean()
    den = float((d * d).sum())
    if den == 0:
        return None
    return float((d[k:] * d[:-k]).sum() / den)


def newey_west_lag(n: int) -> int:
    return int(math.floor(4 * (n / 100) ** (2 / 9)))


def dependence(x: np.ndarray) -> dict[str, Any]:
    """Autocorrelations (lags 1-5), AR(1) and Newey-West effective sizes, long-run variance ratio."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    lags = {str(k): acf(x, k) for k in range(1, ACF_LAGS + 1)}
    r1 = lags["1"]
    n_ar1 = n * (1 - r1) / (1 + r1) if r1 is not None and r1 > 0 else float(n)
    lag = newey_west_lag(n)
    rs = [acf(x, k) for k in range(1, lag + 1)]
    if any(r is None for r in rs):
        f = None
    else:
        f = 1 + 2 * sum((1 - k / (lag + 1)) * r for k, r in enumerate(rs, start=1))
    n_nw = n / f if f is not None and f > 1 else float(n)
    ratio = max(n / n_ar1 if n_ar1 > 0 else math.inf, n / n_nw if n_nw > 0 else math.inf)
    return {"acf": lags, "n": n, "n_ar1": n_ar1, "nw_lag": lag, "long_run_variance_ratio": f, "n_newey_west": n_nw,
            "serial_dependence_high": bool(ratio >= SERIAL_DEPENDENCE_RATIO)}


def _p(v: Any) -> float | None:
    return None if v is None else float(v)


def deflated(sr: float | None, n: int | None, skew: float | None, kurt: float | None, s0: Sr0,
             returns: np.ndarray | None, boot_se: float | None = None) -> dict[str, Any]:
    """All DSR variants for one candidate. Missing values are None with a reason, never a pass."""
    out: dict[str, Any] = {"usage": USAGE, "sr0": s0.value, "sr0_reason": s0.reason}
    if s0.value is None:
        for k in ("dsr", "dsr_ar1", "dsr_newey_west", "dsr_bootstrap_se", "dsr_conservative"):
            out[k], out[f"{k}_reason"] = None, f"SR0 not computable: {s0.reason}"
        return out
    std = psr(sr, n, skew, kurt, s0.value)
    out["dsr"], out["dsr_reason"] = _p(std.value), std.reason
    dep = dependence(returns) if returns is not None and len(returns) >= ACF_LAGS + 2 else None
    out["dependence"] = dep
    for key, nk in (("dsr_ar1", "n_ar1"), ("dsr_newey_west", "n_newey_west")):
        if dep is None:
            out[key], out[f"{key}_reason"] = None, "daily returns missing or too short"
            continue
        n_eff = dep[nk]
        if n_eff < 2:
            out[key], out[f"{key}_reason"] = None, f"effective sample size {n_eff:.2f} < 2"
            continue
        # psr() takes the number of observations; n* is real-valued, the formula uses sqrt(n - 1)
        r = _psr_real_n(sr, n_eff, skew, kurt, s0.value)
        out[key], out[f"{key}_reason"] = r
    if boot_se is None or not (isinstance(boot_se, float) and math.isfinite(boot_se) and boot_se > 0):
        out["dsr_bootstrap_se"] = None
        out["dsr_bootstrap_se_reason"] = "bootstrap not run for this candidate" if boot_se is None else \
            "bootstrap standard error not usable"
    elif sr is None or not math.isfinite(sr):
        out["dsr_bootstrap_se"], out["dsr_bootstrap_se_reason"] = None, "sharpe missing"
    else:
        out["dsr_bootstrap_se"] = float(norm.cdf((sr - s0.value) / boot_se))
        out["dsr_bootstrap_se_reason"] = None
    parts = {k: out[k] for k in ("dsr", "dsr_ar1", "dsr_newey_west", "dsr_bootstrap_se") if out[k] is not None}
    if out["dsr"] is None:
        out["dsr_conservative"], out["dsr_conservative_reason"] = None, out["dsr_reason"]
        out["dsr_conservative_components"] = []
    else:
        out["dsr_conservative"] = min(parts.values())
        out["dsr_conservative_reason"] = None
        out["dsr_conservative_components"] = sorted(parts)
    return out


def _psr_real_n(sr: float | None, n_eff: float, skew: float | None, kurt: float | None,
                s0: float) -> tuple[float | None, str | None]:
    if any(v is None or not math.isfinite(v) for v in (sr, skew, kurt)):
        return None, "sharpe, skewness or kurtosis missing or not finite"
    v = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if not (v > 0 and math.isfinite(v)):
        return None, f"non-positive variance term {v!r}"
    return float(norm.cdf((sr - s0) * math.sqrt(n_eff - 1) / math.sqrt(v))), None


def participation_ratio(returns: np.ndarray) -> dict[str, Any]:
    """Reference only (not used for SR0): N_eff = (sum lambda)^2 / sum lambda^2 of the return correlation matrix.
    returns: days x trials. Columns with zero variance are dropped. With more trials than days the matrix is
    rank-deficient and the value is unstable (P6_PLAN §2.2)."""
    x = np.asarray(returns, dtype=float)
    keep = x.std(axis=0) > 0
    x = x[:, keep]
    if x.shape[1] < 2:
        return {"value": None, "reason": "fewer than 2 trials with variance", "trials": int(x.shape[1])}
    lam = np.linalg.eigvalsh(np.corrcoef(x, rowvar=False))
    lam = np.clip(lam, 0, None)
    return {"value": float(lam.sum() ** 2 / (lam ** 2).sum()), "reason": None, "trials": int(x.shape[1]),
            "days": int(x.shape[0]), "rank_deficient": bool(x.shape[1] > x.shape[0])}
