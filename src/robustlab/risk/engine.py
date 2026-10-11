"""Path engine of P9 (P9_PLAN §2.2-§3.1, C77-C79). No MT5, no database: arrays in, numbers out.

Per day and PER LOT (account currency): x = close-to-close P&L, d = intraday dip below the lower of the two closes
(<= 0), c = closing deals, pos = positions open (max of the day). Everything is divided by the equity E0 before it
reaches the critical-lot functions, so k is in lots and the limits are fractions of E0. k lots are held for the
whole horizon (fixed lot, no compounding, ARCHITECTURE §12.1):
  close equity        1 + k*C_t,  C_t = x_1 + ... + x_t
  intraday low        1 + k*M_t,  M_t = C_{t-1} + min(0, x_t) + d_t
  drawdown            k*(P_t - M_t) / (1 + k*P_t),  P_t = max(0, C_1 .. C_{t-1})       (from the close peak)
  ruin                1 + k*M_t <= 1 - ruin
  stop-out (percent)  1 + k*M_t <= so * k * m * pos_t          m = margin per lot / E0
  stop-out (money)    1 + k*M_t <= so_money / E0
Each is monotone in k on a fixed path, so every path has a critical lot (the smallest k that breaks the limit) and
P(broken at k) = share of paths whose critical lot <= k. The allowed lot for "P <= p" is the largest k whose one-sided
95% upper Clopper-Pearson bound of that share is <= p (Monte Carlo error counts against the limit).

Scenarios (P9_PLAN §2.2; S2-S6, S63 and SC are this platform's own extension: the stress sizes are fixed
assumptions, not market measurements):
  S0  stationary bootstrap per period (Politis-White block length via arch; blocks never cross a period boundary)
  S1b daily shuffle with intraday dips (limits)       S1a classic trade shuffle, closed P&L only (REFERENCE ONLY)
  S2  losing streak    S3 AR(1) rho 0.3 on the demeaned P&L    S4 63-day volatility x2    S5 spread x2
  S6  no edge (mean removed)    S63 = S6 + S3 (no edge with autocorrelation; added by decision Q7)
  SC  S6 + S3 + S4 + S2 + S5 together (ruin and stop-out only)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

import numpy as np
from arch.bootstrap import optimal_block_length
from scipy.stats import beta

ENGINE_VERSION = "risk1"
CONF = 0.95
LIMITED_DD = ("S0", "S1b", "S2", "S3", "S4", "S5", "S6", "S63")   # P(DD > L) <= alpha in each
LIMITED_SURVIVAL = LIMITED_DD + ("SC",)                            # ruin and stop-out in each
REFERENCE = ("S1a",)                                               # reported, never limits anything
SCENARIOS = ("S0", "S1a", "S1b", "S2", "S3", "S4", "S5", "S6", "S63", "SC")


@dataclass(frozen=True)
class History:
    """Per-lot daily series of one candidate, periods concatenated (seg_lens tells where each period ends)."""
    x: np.ndarray
    d: np.ndarray
    c: np.ndarray
    pos: np.ndarray
    seg_lens: tuple[int, ...]
    trades: np.ndarray            # closed P&L per lot of each trade (closing deal), in time order

    def __post_init__(self) -> None:
        n = sum(self.seg_lens)
        if not (len(self.x) == len(self.d) == len(self.c) == len(self.pos) == n):
            raise ValueError("history arrays and seg_lens disagree")


@dataclass(frozen=True)
class Stress:
    streak_factor: float = 1.5
    ar_rho: float = 0.3
    shock_days: int = 63
    shock_factor: float = 2.0
    spread_factor: float = 2.0


# ---------- critical lots ----------

def lows(x: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = np.cumsum(x, axis=1)
    prev = np.concatenate([np.zeros((x.shape[0], 1)), c[:, :-1]], axis=1)
    m = prev + np.minimum(0.0, x) + d
    peak = np.maximum.accumulate(np.maximum(prev, 0.0), axis=1)
    return m, peak


def _inv(worst: np.ndarray, num: float | np.ndarray = 1.0) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return np.where(worst > 0, num / np.where(worst > 0, worst, 1.0), np.inf)


def crit_dd(x: np.ndarray, d: np.ndarray, lim: float) -> np.ndarray:
    m, p = lows(x, d)
    den = p * (1 - lim) - m
    with np.errstate(divide="ignore"):
        k = np.where(den > 0, lim / np.where(den > 0, den, 1.0), np.inf)
    return k.min(axis=1)


def crit_ruin(x: np.ndarray, d: np.ndarray, ruin: float) -> np.ndarray:
    m, _ = lows(x, d)
    return _inv((-m).max(axis=1), ruin)


def crit_stop_out(x: np.ndarray, d: np.ndarray, pos: np.ndarray, *, so_percent: float | None = None,
                  margin: float | None = None, so_money_frac: float | None = None) -> np.ndarray:
    """so_percent: stop-out level in % of margin (margin = margin per lot / E0); so_money_frac: level / E0."""
    m, _ = lows(x, d)
    if so_money_frac is not None:
        return _inv((-m).max(axis=1), 1.0 - so_money_frac) if so_money_frac < 1 else np.zeros(x.shape[0])
    return _inv((so_percent / 100.0 * margin * pos - m).max(axis=1))


def c_max(n: int, p: float, conf: float = CONF) -> int:
    """Largest count whose one-sided upper Clopper-Pearson bound is <= p (-1 if none)."""
    c = int(n * p)
    while c >= 0 and beta.ppf(conf, c + 1, n - c) > p:
        c -= 1
    return c


def allowed(crit: np.ndarray, p: float) -> float:
    """Largest lot (supremum) for which the upper bound of P(critical lot <= k) stays <= p."""
    s = np.sort(crit)
    c = c_max(len(s), p)
    if c < 0:
        return 0.0
    # at k = s[c] itself c + 1 paths break the limit, so stay strictly below it
    return float(np.nextafter(s[c], 0.0) * (1 - 1e-9)) if np.isfinite(s[c]) else float("inf")


def upper_bound(count: int, n: int, conf: float = CONF) -> float:
    return 1.0 if count >= n else float(beta.ppf(conf, count + 1, n - count))


# ---------- scenarios ----------

def block_length(seg: np.ndarray) -> float:
    return max(1.0, float(optimal_block_length(seg)["stationary"].iloc[0])) if len(seg) >= 2 else 1.0


def mean_block_length(h: History) -> float:
    ends = np.cumsum(h.seg_lens)
    return float(np.mean([block_length(h.x[e - n:e]) for e, n in zip(ends, h.seg_lens, strict=True)]))


def sb_index(seg_lens: tuple[int, ...], b: float, n_paths: int, horizon: int, rng: np.random.Generator) -> np.ndarray:
    """Stationary-bootstrap indices into the concatenated periods; a block never runs past its period's end."""
    ends = np.cumsum(seg_lens)
    seg_end_of = np.repeat(ends, seg_lens)
    n = int(ends[-1])
    idx = np.empty((n_paths, horizon), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_paths)
    for t in range(1, horizon):
        nxt = idx[:, t - 1] + 1
        jump = (rng.random(n_paths) < 1.0 / b) | (nxt >= seg_end_of[idx[:, t - 1]])
        idx[:, t] = np.where(jump, rng.integers(0, n, n_paths), nxt)
    return idx


def longest_losing_run(x: np.ndarray, seg_lens: tuple[int, ...]) -> int:
    best, pos = 0, 0
    for n in seg_lens:
        run = 0
        for v in x[pos:pos + n]:
            run = run + 1 if v < 0 else 0
            best = max(best, run)
        pos += n
    return best


def _ar(z: np.ndarray, center: float, rho: float) -> np.ndarray:
    e = np.empty_like(z)
    u = z - center
    e[:, 0] = u[:, 0]
    s = math.sqrt(1 - rho * rho)
    for t in range(1, z.shape[1]):
        e[:, t] = rho * e[:, t - 1] + s * u[:, t]
    return center + e


def scenarios(h: History, rng: np.random.Generator, n_paths: int, horizon: int, cost_per_closing: float,
              stress: Stress = Stress()) -> Iterator[tuple[str, np.ndarray, np.ndarray, np.ndarray]]:
    """Yields (name, X, D, P) per scenario, per lot in account currency. Draw order is fixed (reproducible)."""
    x, d, c, pos = h.x, h.d, h.c, h.pos
    mu = float(x.mean())
    b = mean_block_length(h)
    idx = sb_index(h.seg_lens, b, n_paths, horizon, rng)
    X, D, C, P = x[idx], d[idx], c[idx], pos[idx]
    yield "S0", X, D, P
    j = rng.integers(0, len(x), (n_paths, horizon))
    yield "S1b", x[j], d[j], pos[j]
    if len(h.trades):
        per_day = max(len(h.trades) / len(x), 1e-12)
        nt = max(1, int(round(per_day * horizon)))
        jt = rng.integers(0, len(h.trades), (n_paths, nt))
        yield "S1a", h.trades[jt], np.zeros((n_paths, nt)), np.zeros((n_paths, nt))

    k = min(horizon, math.ceil(stress.streak_factor * longest_losing_run(x, h.seg_lens)))
    losers = np.flatnonzero(x < 0)

    def streak(X: np.ndarray, D: np.ndarray, P: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        X, D, P = X.copy(), D.copy(), P.copy()
        if k == 0 or len(losers) == 0:
            return X, D, P
        st = rng.integers(0, horizon - k + 1, n_paths)
        pick = losers[rng.integers(0, len(losers), (n_paths, k))]
        rows = np.arange(n_paths)[:, None]
        cols = st[:, None] + np.arange(k)[None, :]
        X[rows, cols], D[rows, cols], P[rows, cols] = x[pick], d[pick], pos[pick]
        return X, D, P

    def shock(X: np.ndarray, D: np.ndarray, center: float) -> tuple[np.ndarray, np.ndarray]:
        w = min(stress.shock_days, horizon)
        st = rng.integers(0, horizon - w + 1, n_paths)
        t = np.arange(horizon)[None, :]
        mask = (t >= st[:, None]) & (t < st[:, None] + w)
        return (np.where(mask, center + stress.shock_factor * (X - center), X),
                np.where(mask, stress.shock_factor * D, D))

    yield ("S2", *streak(X, D, P))
    yield "S3", _ar(X, mu, stress.ar_rho), D, P
    yield ("S4", *shock(X, D, mu), P)
    yield "S5", X - stress.spread_factor * cost_per_closing * C, D, P
    yield "S6", X - mu, D, P
    yield "S63", _ar(X - mu, 0.0, stress.ar_rho), D, P
    Z = _ar(X - mu, 0.0, stress.ar_rho)
    Z, Dc = shock(Z, D, 0.0)
    Z, Dc, Pc = streak(Z, Dc, P)
    yield "SC", Z - stress.spread_factor * cost_per_closing * C, Dc, Pc


# ---------- per-lot risk profile (does not depend on the equity) ----------

def path_profile(X: np.ndarray, D: np.ndarray) -> dict[str, float]:
    """Amounts per 1 lot: max drawdown (from the close peak to the intraday low), underwater days, losing days."""
    m, p = lows(X, D)
    dd = (p - m).max(axis=1)
    c = np.cumsum(X, axis=1)
    peak = np.maximum.accumulate(np.maximum(c, 0.0), axis=1)
    under = c < peak
    longest = _longest_true(under)
    streak = _longest_true(X < 0)
    return {"max_dd_per_lot_q50": float(np.quantile(dd, 0.50)), "max_dd_per_lot_q95": float(np.quantile(dd, 0.95)),
            "max_dd_per_lot_q99": float(np.quantile(dd, 0.99)), "underwater_days_q95": float(np.quantile(longest, 0.95)),
            "share_below_peak_at_end": float(under[:, -1].mean()),
            "losing_days_run_q95": float(np.quantile(streak, 0.95))}


def _longest_true(a: np.ndarray) -> np.ndarray:
    best = np.zeros(a.shape[0])
    run = np.zeros(a.shape[0])
    for t in range(a.shape[1]):
        run = np.where(a[:, t], run + 1, 0)
        best = np.maximum(best, run)
    return best
