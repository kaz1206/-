"""Probabilistic Sharpe Ratio and Minimum Track Record Length (Bailey & Lopez de Prado 2012) [S-PSR].

Same formulas as R PerformanceAnalytics 2.1.0 `ProbSharpeRatio` / `MinTrackRecord` [S-PA-R], with the
kurtosis term always used (R's default `ignore_kurtosis = TRUE` fixes it to 3; we follow the paper, C54):

    PSR(SR*)  = Phi( (SR - SR*) * sqrt(n - 1) / sqrt(1 - g3*SR + (g4 - 1)/4 * SR^2) )
    MinTRL    = 1 + (1 - g3*SR + (g4 - 1)/4 * SR^2) * (z_p / (SR - SR*))^2

SR is the per-period (not annualized) Sharpe ratio with the sample standard deviation, g3 the skewness
and g4 the (non-excess) kurtosis, both by the moment method. Inputs are defined in P4_PLAN §3.4 (C53).
Invalid inputs give None plus a reason; callers must treat None as a failure, never as a pass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

PSR_VERSION = "psr1"


@dataclass(frozen=True)
class PsrResult:
    value: float | None
    reason: str | None = None  # why value is None


def _finite(*xs: float | None) -> bool:
    return all(x is not None and isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def _variance_term(sr: float, skew: float, kurt: float) -> float:
    return 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr


def psr(sr: float | None, n: int | None, skew: float | None, kurt: float | None, sr_star: float = 0.0) -> PsrResult:
    if not _finite(sr, skew, kurt, sr_star) or n is None:
        return PsrResult(None, "sharpe, skewness or kurtosis missing or not finite")
    if n < 2:
        return PsrResult(None, "fewer than 2 observations")
    v = _variance_term(sr, skew, kurt)
    if not (v > 0 and math.isfinite(v)):
        return PsrResult(None, f"non-positive variance term {v!r}")
    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(v)
    return PsrResult(float(norm.cdf(z)))


def min_trl(sr: float | None, skew: float | None, kurt: float | None, sr_star: float = 0.0,
            prob: float = 0.95) -> PsrResult:
    """Observations needed for PSR(sr_star) to reach `prob`. None when SR <= SR* (never reachable)."""
    if not _finite(sr, skew, kurt, sr_star):
        return PsrResult(None, "sharpe, skewness or kurtosis missing or not finite")
    if sr <= sr_star:
        return PsrResult(None, "observed Sharpe does not exceed the benchmark")
    v = _variance_term(sr, skew, kurt)
    if not (v > 0 and math.isfinite(v)):
        return PsrResult(None, f"non-positive variance term {v!r}")
    return PsrResult(1.0 + v * (norm.ppf(prob) / (sr - sr_star)) ** 2)
