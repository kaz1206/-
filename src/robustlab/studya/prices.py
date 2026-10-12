"""Continuous synthetic price paths for study A (SPEC §4, §10 A2).

Step returns = common base (the P10 H0 model values: zero-drift GARCH(1,1) bar volatility, Student-t steps)
+ an edge component added OUTSIDE the GARCH recursion, so the volatility path is the same with and without the edge.
Price path: MULTIPLICATIVE martingale p_t = p_{t-1} (1 + r_t) (SPEC change s3-C1): the P10 arithmetic path drifts to
unrealistic and even negative prices over the ~20 years study A needs (2 of 300 base paths went below 0). With
E[r_t | past] = 0 the price stays a martingale, so any fixed-lot rule still has expected gross P&L 0 under H0.
The base of round i depends only on (base_seed, i); the edge only on (edge_seed, i): S round i and P round i share the
base (common random numbers, SPEC §4.2). Strength 0 gives the base bit for bit.
The draw order of the random numbers is part of the specification (results are reproduced from the seeds).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np

PRICES_VERSION = "pa2"


@dataclass(frozen=True)
class PriceModel:
    sigma_bar: float
    garch_alpha: float
    garch_beta: float
    t_df: int
    bars_per_day: int
    steps_per_bar: int


def weekdays(first: dt.date, end_exclusive: dt.date) -> list[dt.date]:
    out, d = [], first
    while d < end_exclusive:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def weekdays_before(day: dt.date, n: int) -> dt.date:
    """The n-th weekday before `day` (the first day of n weekdays of history)."""
    d, k = day, 0
    while k < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            k += 1
    return d


def base_steps(n_bars: int, pm: PriceModel, seed: int, round_index: int) -> np.ndarray:
    """(n_bars * steps_per_bar,) zero-drift relative step returns of round `round_index`."""
    rng = np.random.default_rng([seed, round_index, 0])
    spb, df = pm.steps_per_bar, pm.t_df
    z = rng.standard_t(df, (n_bars, spb)) / math.sqrt(df / (df - 2))
    a, b = pm.garch_alpha, pm.garch_beta
    om = (1 - a - b) * pm.sigma_bar ** 2
    h = pm.sigma_bar ** 2
    e_prev = 0.0
    out = np.empty((n_bars, spb))
    root = math.sqrt(spb)
    for t in range(n_bars):
        h = om + a * e_prev * e_prev + b * h
        shock = math.sqrt(h) * z[t] / root
        out[t] = shock
        e_prev = float(shock.sum())
    return out.reshape(-1)


def edge_unit(kind: str, n_bars: int, spb: int, seed: int, round_index: int, *, persistence_bars: float = 0.0,
              half_life_bars: float = 0.0) -> np.ndarray:
    """(n_bars * spb,) edge component per step with unit scale; the strength multiplies it (SPEC §4.2).

    regime  (E1): +1/-1 Markov regime per bar, mean length `persistence_bars`; per step regime / spb.
    ou_drift (E2): AR(1) drift per bar with half-life `half_life_bars`, stationary sd 1; per step drift / spb.
    """
    rng = np.random.default_rng([seed, round_index, 1])
    if kind == "regime":
        s0 = 1.0 if rng.random() < 0.5 else -1.0
        flips = rng.random(n_bars) < 1.0 / persistence_bars
        bar = s0 * np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0)
    elif kind == "ou_drift":
        phi = 0.5 ** (1.0 / half_life_bars)
        eta = rng.standard_normal(n_bars) * math.sqrt(1 - phi * phi)
        bar = np.empty(n_bars)
        x = float(rng.standard_normal())
        for t in range(n_bars):
            x = phi * x + eta[t]
            bar[t] = x
    else:
        raise ValueError(f"unknown edge kind {kind}")
    return np.repeat(bar / spb, spb)


def path(steps: np.ndarray, start: float) -> np.ndarray:
    """Multiplicative price path (len(steps) + 1,) rounded to 5 digits (the exported / tested prices)."""
    if (steps <= -1).any():
        raise ValueError("a step return <= -100%: the price model is out of range")
    p = np.round(np.concatenate([[start], start * np.cumprod(1.0 + steps)]), 5)
    if p.min() <= 0:
        raise ValueError("the price path reached 0 at 5 digits: the price model is out of range")
    return p
