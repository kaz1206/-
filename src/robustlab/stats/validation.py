"""Validation-period evidence: per-period aggregation, the serial-dependence-corrected p-value, Holm
(P8_PLAN §4.2, §4.2.1, §4.4; C71-C73).

- Periods are independent single tests, each starting from the deposit D. No return crosses a boundary and the
  gap between periods gets no zero returns. Mean / sd / skewness / kurtosis come from the pooled sample of daily
  returns; autocorrelations only use pairs inside a period; the stationary bootstrap resamples each period
  separately (blocks never cross a boundary).
- p = 1 - min(PSR standard, PSR AR(1) n*, PSR Newey-West n*, Phi(SR / SE_bootstrap)), SR* = 0, one-sided.
  If the standard PSR cannot be computed (fewer than 30 days, zero variance), p = 1 (no evidence).
- Holm step-down at family-wise alpha. Checked against statsmodels' multipletests in the tests (H2).
- Known limitation: under AR(2)-type dependence the p-value is too small (P(p <= 0.01) = 2.5% in H1). Validity
  under the other tested forms: P8_PLAN §4.4.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from arch.bootstrap import StationaryBootstrap, optimal_block_length
from scipy.stats import kurtosis, norm, skew

from robustlab.stats.dsr import ACF_LAGS, SERIAL_DEPENDENCE_RATIO, newey_west_lag

EVIDENCE_VERSION = "ev1"
MIN_DAYS = 30
ALPHA = 0.05
KNOWN_LIMITATION = ("Under AR(2)-type dependence the p-value is too small (P8 check H1: P(p <= 0.01) = 2.5%, "
                    "P(p <= 0.05) = 7.8%; Holm family error 8.95% with K = 5, correlation 0.5). A pass is "
                    "evidence against chance under the tested forms only; a fail means insufficient evidence, "
                    "not 'no edge'.")


def acf_segments(segs: list[np.ndarray], k: int) -> float | None:
    """Autocorrelation at lag k from pairs inside each period, around the pooled mean (§4.2.1)."""
    allx = np.concatenate(segs)
    m = allx.mean()
    den = float(((allx - m) ** 2).sum())
    if den == 0:
        return None
    num = sum(float(((s[k:] - m) * (s[:-k] - m)).sum()) for s in segs if len(s) > k)
    return num / den


def _psr(sr: float, n_eff: float, g3: float, g4: float) -> float | None:
    v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    if not (v > 0) or n_eff < 2:
        return None
    return float(norm.cdf(sr * math.sqrt(n_eff - 1) / math.sqrt(v)))


def boot_se(segs: list[np.ndarray], reps: int, rng: np.random.Generator) -> float | None:
    """Sharpe standard error: stationary bootstrap per period (Politis-White block length per period, >= 1)."""
    gens = []
    for s in segs:
        b = max(1.0, float(optimal_block_length(s)["stationary"].iloc[0])) if len(s) >= 2 else 1.0
        gens.append(StationaryBootstrap(b, s, seed=rng).bootstrap(reps))
    out = []
    for draws in zip(*gens, strict=True):
        z = np.concatenate([d[0][0] for d in draws])
        sd = z.std(ddof=1)
        if sd > 0:
            out.append(z.mean() / sd)
    return float(np.std(out, ddof=1)) if len(out) >= 2 else None


def evidence(segs: list[np.ndarray], reps: int, rng: np.random.Generator) -> dict[str, Any]:
    """The p-value of one candidate and everything it was built from."""
    segs = [np.asarray(s, dtype=float) for s in segs]
    x = np.concatenate(segs) if segs else np.array([])
    n = len(x)
    out: dict[str, Any] = {"version": EVIDENCE_VERSION, "n": n, "n_per_period": [len(s) for s in segs],
                           "reps": reps}
    if n < MIN_DAYS or not np.all(np.isfinite(x)) or float(x.std(ddof=1)) == 0:
        return out | {"p": 1.0, "reason": f"no evidence: fewer than {MIN_DAYS} days, non-finite values or zero "
                                          "variance", "psr": {}}
    sd = float(x.std(ddof=1))
    sr, g3, g4 = float(x.mean() / sd), float(skew(x)), float(kurtosis(x, fisher=False))
    std = _psr(sr, n, g3, g4)
    if std is None:
        return out | {"p": 1.0, "reason": "no evidence: non-positive PSR variance term", "psr": {},
                      "sharpe": sr, "skewness": g3, "kurtosis": g4}
    psr = {"standard": std}
    r1 = acf_segments(segs, 1)
    n_ar1 = n * (1 - r1) / (1 + r1) if r1 is not None and r1 > 0 else float(n)
    lag = newey_west_lag(n)
    rs = [acf_segments(segs, k) for k in range(1, lag + 1)]
    f = 1 + 2 * sum((1 - k / (lag + 1)) * r for k, r in enumerate(rs, start=1)) if None not in rs else None
    n_nw = n / f if f is not None and f > 1 else float(n)
    for name, ne in (("ar1", n_ar1), ("newey_west", n_nw)):
        v = _psr(sr, ne, g3, g4)
        if v is not None:
            psr[name] = v
    se = boot_se(segs, reps, rng)
    if se and se > 0:
        psr["bootstrap_se"] = float(norm.cdf(sr / se))
    ratio = max(n / n_ar1, n / n_nw)
    return out | {
        "p": 1.0 - min(psr.values()), "reason": None, "psr": psr, "sharpe": sr, "skewness": g3, "kurtosis": g4,
        "acf": {str(k): acf_segments(segs, k) for k in range(1, ACF_LAGS + 1)}, "n_ar1": n_ar1, "nw_lag": lag,
        "long_run_variance_ratio": f, "n_newey_west": n_nw, "bootstrap_se": se,
        "serial_dependence_high": bool(ratio >= SERIAL_DEPENDENCE_RATIO),
    }


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, bool]:
    """Holm step-down: ascending p (ties by id), reject while p_(i) <= alpha / (K - i + 1), then stop."""
    order = sorted(pvalues, key=lambda c: (pvalues[c], c))
    k = len(order)
    out = dict.fromkeys(pvalues, False)
    for i, c in enumerate(order):
        if pvalues[c] <= alpha / (k - i):
            out[c] = True
        else:
            break
    return out
