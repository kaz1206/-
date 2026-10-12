"""H0 price paths and EA archetype simulators for P10 (P10_PLAN §1.1-§1.2, §1.4).

Price: ARITHMETIC path (a martingale, C1) of steps, `steps_per_bar` per H4 bar; bar volatility GARCH(1,1), steps
Student t, zero drift. Orders fill at the step price that crossed their level (C3); positions are decided from the
past only (C2); equity is marked to market (C4); positions are bounded by max levels and the stop-out (C5); costs are
counted separately (C6). Under these conditions any rule has expected gross P&L 0. This is a model: steps are coarser
than ticks, gaps / slippage and the broker's stop-out processing are not modelled (P10_PLAN §1.4).
The draw order of the random numbers is part of the specification (the reproduction check depends on it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

SIM_VERSION = "sim2"


@dataclass(frozen=True)
class Account:
    deposit: float
    leverage: float
    contract: float
    pip: float
    spread: float
    stop_out_pct: float
    start_price: float


@dataclass(frozen=True)
class PriceModel:
    sigma_bar: float
    garch_alpha: float
    garch_beta: float
    t_df: int
    bars_per_day: int
    steps_per_bar: int


def prices(n_rounds: int, n_days: int, rng: np.random.Generator, pm: PriceModel, start: float,
           ar: float = 0.0) -> np.ndarray:
    """(n_rounds, steps + 1) price paths from `start`. ar != 0 only for the power runs (bar-return AR(1))."""
    spb = pm.steps_per_bar
    T = n_days * pm.bars_per_day
    a, b = pm.garch_alpha, pm.garch_beta
    om = (1 - a - b) * pm.sigma_bar ** 2
    h = np.full(n_rounds, pm.sigma_bar ** 2)
    e_prev = np.zeros(n_rounds)
    r_prev = np.zeros(n_rounds)
    df = pm.t_df
    z = rng.standard_t(df, (n_rounds, T, spb)) / math.sqrt(df / (df - 2))
    steps = np.empty((n_rounds, T, spb))
    k = math.sqrt(1 - ar * ar)
    for t in range(T):
        h = om + a * e_prev ** 2 + b * h
        sd = np.sqrt(h)
        shock = sd[:, None] * z[:, t, :] / math.sqrt(spb) * k
        steps[:, t, :] = shock + (ar * r_prev / spb)[:, None]
        e_prev = shock.sum(axis=1) / k if ar else shock.sum(axis=1)
        r_prev = steps[:, t, :].sum(axis=1)
    inc = start * steps.reshape(n_rounds, T * spb)
    return np.concatenate([np.full((n_rounds, 1), start), start + np.cumsum(inc, axis=1)], axis=1)


@dataclass
class Book:
    n: int
    deposit: float
    bal: np.ndarray = field(init=False)
    dead: np.ndarray = field(init=False)
    trades: np.ndarray = field(init=False)
    wins: np.ndarray = field(init=False)
    cost: np.ndarray = field(init=False)
    stop_outs: np.ndarray = field(init=False)
    maxpos: np.ndarray = field(init=False)
    minml: np.ndarray = field(init=False)
    eq_close: list = field(default_factory=list)
    eq_min: list = field(default_factory=list)
    closed: list = field(init=False)

    def __post_init__(self) -> None:
        n = self.n
        self.bal = np.full(n, self.deposit)
        self.dead = np.zeros(n, bool)
        self.trades = np.zeros(n, int)
        self.wins = np.zeros(n, int)
        self.cost = np.zeros(n)
        self.stop_outs = np.zeros(n, int)
        self.maxpos = np.zeros(n, int)
        self.minml = np.full(n, np.inf)
        self.closed = [[] for _ in range(n)]

    def close(self, mask: np.ndarray, pnl: np.ndarray, npos: np.ndarray) -> None:
        idx = np.flatnonzero(mask)
        self.bal[idx] += pnl[idx]
        self.trades[idx] += npos[idx]
        self.wins[idx] += (pnl[idx] > 0) * npos[idx]
        for i in idx:
            self.closed[i].append(float(pnl[i]))


def run_trend(path: np.ndarray, fast: np.ndarray, slow: np.ndarray, lot: float, acc: Account, bars_per_day: int,
              spb: int) -> Book:
    """A1: MA cross on bar closes, decided at bar ends, always in the market, fixed lot, marked every step."""
    R, S1 = path.shape
    P = len(fast)
    n = R * P
    rr = np.repeat(np.arange(R), P)
    fa, sl = np.tile(fast, R), np.tile(slow, R)
    closes = path[:, ::spb]
    cs = np.concatenate([np.zeros((R, 1)), np.cumsum(closes, axis=1)], axis=1)
    units = lot * acc.contract
    bk = Book(n, acc.deposit)
    pos = np.zeros(n)
    entry = np.zeros(n)
    day_min = np.full(n, np.inf)
    steps_per_day = bars_per_day * spb
    for s in range(1, S1):
        px = path[rr, s]
        eq = bk.bal + pos * units * (px - entry)
        day_min = np.minimum(day_min, eq)
        if (pos != 0).any():
            bk.minml = np.minimum(bk.minml, np.where(pos != 0, eq / (units * px / acc.leverage) * 100, np.inf))
        if s % spb == 0:
            k = s // spb
            ok = k >= sl
            maf = np.where(ok, (cs[rr, k + 1] - cs[rr, np.maximum(k + 1 - fa, 0)]) / fa, 0)
            mas = np.where(ok, (cs[rr, k + 1] - cs[rr, np.maximum(k + 1 - sl, 0)]) / sl, 0)
            new = np.where(ok, np.sign(maf - mas), 0)
            ch = new != pos
            bk.close(ch & (pos != 0), pos * units * (px - entry), np.ones(n, int))
            opn = ch & (new != 0)
            bk.bal[opn] -= acc.spread * units
            bk.cost[opn] += acc.spread * units
            entry = np.where(opn, px, entry)
            pos = np.where(ch, new, pos)
            bk.maxpos = np.maximum(bk.maxpos, (pos != 0).astype(int))
        if s % steps_per_day == 0:
            bk.eq_close.append(bk.bal + pos * units * (px - entry))
            bk.eq_min.append(day_min.copy())
            day_min[:] = np.inf
    return bk


def run_grid(path: np.ndarray, g_pips: np.ndarray, tp_pips: np.ndarray, mult: np.ndarray, base: float,
             max_levels: int, acc: Account, bars_per_day: int, spb: int) -> Book:
    """A2 / A3: first entry at a new bar (contrarian to the last bar), grid adds, basket TP and stop-out at every
    step, filled at the step price; level k trades base * mult^k."""
    R, S1 = path.shape
    P = len(g_pips)
    n = R * P
    rr = np.repeat(np.arange(R), P)
    g, tp, m = np.tile(g_pips, R) * acc.pip, np.tile(tp_pips, R) * acc.pip, np.tile(mult, R)
    C, lev, spread = acc.contract, acc.leverage, acc.spread
    bk = Book(n, acc.deposit)
    lv = np.zeros(n, int)
    d = np.zeros(n)
    last = np.zeros(n)
    sl_ = np.zeros(n)
    slp = np.zeros(n)
    day_min = np.full(n, np.inf)
    steps_per_day = bars_per_day * spb
    for s in range(0, S1):
        px = path[rr, s]
        if s % spb == 0 and s < S1 - 1:
            start = (lv == 0) & ~bk.dead
            if start.any():
                need = base * C * px / lev
                can = start & (bk.bal > need)
                prev = path[rr, s - spb] if s >= spb else px
                dirn = np.where(px >= prev, -1.0, 1.0)
                d = np.where(can, dirn, d)
                lv = np.where(can, 1, lv)
                last = np.where(can, px, last)
                sl_ = np.where(can, base, sl_)
                slp = np.where(can, base * px, slp)
                bk.bal[can] -= spread * base * C
                bk.cost[can] += spread * base * C
                bk.dead |= start & ~can
        if s == 0:
            continue
        act = lv > 0
        hit = act & (lv < max_levels) & np.where(d > 0, px <= last - g, px >= last + g)
        if hit.any():
            lot = base * m ** lv
            sl_ = np.where(hit, sl_ + lot, sl_)
            slp = np.where(hit, slp + lot * px, slp)
            last = np.where(hit, px, last)
            lv = np.where(hit, lv + 1, lv)
            bk.bal[hit] -= spread * lot[hit] * C
            bk.cost[hit] += spread * lot[hit] * C
        fl = np.where(act, d * C * (sl_ * px - slp), 0.0)
        eq = bk.bal + fl
        margin = sl_ * C * px / lev
        ml = np.where(act & (margin > 0), eq / np.where(margin > 0, margin, 1) * 100, np.inf)
        bk.minml = np.minimum(bk.minml, ml)
        so = act & (ml <= acc.stop_out_pct)
        if so.any():
            bk.close(so, fl, lv)
            bk.stop_outs[so] += lv[so]
            lv = np.where(so, 0, lv)
            sl_ = np.where(so, 0, sl_)
            slp = np.where(so, 0, slp)
            fl = np.where(so, 0.0, fl)
        bk.maxpos = np.maximum(bk.maxpos, lv)
        act = lv > 0
        avg = np.where(act, slp / np.where(sl_ > 0, sl_, 1), 0)
        win = act & np.where(d > 0, px >= avg + tp, px <= avg - tp)
        if win.any():
            bk.close(win, fl, lv)
            lv = np.where(win, 0, lv)
            sl_ = np.where(win, 0, sl_)
            slp = np.where(win, 0, slp)
            fl = np.where(win, 0.0, fl)
        bk.dead |= bk.bal <= 0
        day_min = np.minimum(day_min, bk.bal + fl)
        if s % steps_per_day == 0:
            bk.eq_close.append(bk.bal + fl)
            bk.eq_min.append(day_min.copy())
            day_min[:] = np.inf
    return bk


def simulate(spec: dict[str, Any], path: np.ndarray, acc: Account, pm: PriceModel) -> Book:
    """Run the 5 x 5 grid of one archetype on paths (rounds x steps)."""
    A, B = np.meshgrid(np.array(spec["a"], float), np.array(spec["b"], float), indexing="ij")
    A, B = A.ravel(), B.ravel()
    if spec["type"] == "trend":
        return run_trend(path, A.astype(int), B.astype(int), spec["lot"], acc, pm.bars_per_day, pm.steps_per_bar)
    if spec["type"] == "grid":
        return run_grid(path, A, B, np.ones_like(A), spec["base"], spec["max_levels"], acc, pm.bars_per_day,
                        pm.steps_per_bar)
    if spec["type"] == "martingale":
        return run_grid(path, B, np.full_like(A, float(spec["tp"])), A, spec["base"], spec["max_levels"], acc,
                        pm.bars_per_day, pm.steps_per_bar)
    raise ValueError(f"unknown archetype type {spec['type']}")
