"""P10 plan, pre-implementation simulation: the pipeline's chance pass rate under null hypotheses that keep the EA's
structure (drafted 2026-10-12). Seed 20261012.

WHY NOT A P&L SHUFFLE. A martingale / averaging-down (nanpin) EA makes many small basket wins and, rarely, one huge
loss when the grid runs into a trend and the account is stopped out. Its lot size depends on the path (level k of a
basket trades base * m^k). Shuffling its realised trade P&L (a) keeps only the losses that happened to occur in the
sample - a 2-year backtest without a blow-up contains none, so a shuffle can never create one - and (b) cuts the link
between lot size and the preceding adverse move. The null here therefore RE-RUNS THE EA'S OWN MECHANICS (entries,
grid adds, lot multiplier, basket take-profit, margin, stop-out) on price paths in which nothing is predictable.

Null hypothesis H0 (P10_PLAN §2): the EA's decisions carry no information about future price moves. Implemented as:
the same EA rules run on synthetic H4 price paths with zero drift and no return autocorrelation, but with the
features that matter for these EAs: volatility clustering (GARCH(1,1) 0.05 / 0.93), fat tails (Student t(5)),
intrabar high / low, spread, leverage, margin and stop-out. Under H0 every rule has expected gross P&L 0 (minus
costs); what the pipeline sees in one backtest is luck.

EA archetypes (5 x 5 parameter grid each, one round = one price history, all grid points on it):
  A1 trend   : MA cross, always in the market, fixed 0.10 lot. fast {5,10,15,20,25} x slow {40,60,80,100,120} bars.
  A2 nanpin  : contrarian first entry, add 0.05 lot every g pips against (max 10 levels), close the basket at
               average +/- tp pips. g {10,15,20,30,40} x tp {5,10,15,20,30}. Constant lot.
  A3 martin  : as A2 but level k trades 0.01 * m^k (max 12 levels), tp 10 pips. m {1.2,1.4,1.6,1.8,2.0} x
               g {10,15,20,30,40}.
Account: 10,000 USD, leverage 1:100, EURUSD-like (contract 100,000, start 1.10, pip 0.0001), spread 1 pip, stop-out
at margin level 50% (all positions closed at the step price). Run 2 price model: a continuous arithmetic path with 8
steps per H4 bar; every order fills at the step price that crossed its level (no intrabar-order assumption).

Pipeline (PRODUCTION decision functions; only the EA simulator and the price model are new):
  Discovery 520 days -> P4 robustlab.filtering.gates.evaluate (profile swing_trend for A1, mean_reversion for A2/A3)
  -> P5 robustlab.robustness.grid_stats.compute -> P7 robustlab.selection.core.decide (budget 5; stand-ins: DSR =
  Discovery daily Sharpe, resampled DD = observed equity max DD %) -> P8a on V0 520 days + V1 130 days (each from the
  deposit): robustlab.stats.validation.evidence (2,000 bootstrap reps) + holm (K = number selected), ruin gate
  (bankruptcy or stop-out in V0/V1), pooled profit > 0, profit after 1 pip per closing lot > 0.
  A round is a FALSE PASS if any candidate passes P8a. Passers are then run for one more year (FUT, 260 days).

Checks (acceptance written here BEFORE running):
 S1 simulator sanity under H0: per archetype, the mean GROSS P&L (net + costs) of all Discovery runs is within
    3 standard errors of 0 (rounds as the unit). Plus a hand case per archetype (exact numbers, see hand_cases()).
 S2 chance pass rate (the key P10 number), 400 rounds per archetype under H0: share of rounds with >= 1 P8a pass.
    Proposed P10 criterion: <= 5% for every archetype. Recorded with the Clopper-Pearson 95% interval.
 S3 structure matters (martingale): for A3 lanes that survived Discovery + V0 + V1 without a stop-out, compare
    P(stop-out in the next year) (a) structural: the EA re-run on a fresh H0 year, (b) naive: the lane's own closed
    P&L (Discovery + V0 + V1) shuffled and replayed for a year, ruin = equity <= 50% of the deposit.
    Expectation (not a criterion): (b) much smaller than (a).
 S4 power, recorded only: the same pipeline when the price has the structure the EA lives on (A1: AR(1) +0.05
    momentum; A2 / A3: AR(1) -0.05 mean reversion), 200 rounds.
RUN 2 (2026-10-12): run 1 failed S1 for A2 / A3 (intrabar order bias, see the price-model comment); the same
checks and criteria apply to run 2, which uses a continuous step path.
Usage: uv run python docs/research/p10_simulations/null_sim.py hand | s1:<A> | run:<A>:<null|edge>[:reps]
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
from scipy.stats import beta, norm, kurtosis, skew

from robustlab.config.loader import load_profile
from robustlab.filtering import gates as p4
from robustlab.metrics.engine import daily_metrics
from robustlab.robustness import grid_stats
from robustlab.selection import core
from robustlab.stats import validation as ev

SEED = 20261012
ROOT = Path(__file__).resolve().parents[3]
BARS_PER_DAY = 6
DEP, LEV, CONTRACT, PIP, SPREAD, SO = 10_000.0, 100.0, 100_000.0, 0.0001, 0.0001, 50.0
SIGMA_BAR = 0.0022
DAYS = {"D": 520, "V0": 520, "V1": 130, "F": 260}
GRID = {
    "A1": ([5, 10, 15, 20, 25], [40, 60, 80, 100, 120]),
    "A2": ([10, 15, 20, 30, 40], [5, 10, 15, 20, 30]),
    "A3": ([1.2, 1.4, 1.6, 1.8, 2.0], [10, 15, 20, 30, 40]),
}
PROFILE = {"A1": "swing_trend", "A2": "mean_reversion", "A3": "mean_reversion"}
AXES = {"A1": ["fast", "slow"], "A2": ["grid", "tp"], "A3": ["mult", "grid"]}


# ---------- price model (run 2: one continuous path, 8 steps per H4 bar) ----------
# Run 1 used synthetic bar high / low with an assumed intrabar order, which handed contrarian grids a free
# dip-and-rebound inside every bar (S1 failed). Run 2 has no intrabar ambiguity: the price is a path of steps, every
# order fills at the step price that crossed its level, and the price is ARITHMETIC (a martingale), so any rule that
# uses only the past has expected gross P&L exactly 0.
SPB = 8  # steps per H4 bar


def prices(n_rounds: int, n_days: int, rng: np.random.Generator, ar: float = 0.0, spb: int = SPB):
    """(n_rounds, steps + 1) price path from 1.10; GARCH(1,1)-t(5) per H4 bar, t(5) steps inside; ar on bar returns
    (power runs only), spread evenly over the next bar."""
    T = n_days * BARS_PER_DAY
    a, b = 0.05, 0.93
    om = (1 - a - b) * SIGMA_BAR ** 2
    h = np.full(n_rounds, SIGMA_BAR ** 2)
    e_prev = np.zeros(n_rounds)
    r_prev = np.zeros(n_rounds)
    z = rng.standard_t(5, (n_rounds, T, spb)) / math.sqrt(5 / 3)
    steps = np.empty((n_rounds, T, spb))
    for t in range(T):
        h = om + a * e_prev ** 2 + b * h
        sd = np.sqrt(h)
        shock = sd[:, None] * z[:, t, :] / math.sqrt(spb) * math.sqrt(1 - ar * ar)
        steps[:, t, :] = shock + (ar * r_prev / spb)[:, None]
        e_prev = shock.sum(axis=1) / math.sqrt(1 - ar * ar) if ar else shock.sum(axis=1)
        r_prev = steps[:, t, :].sum(axis=1)
    inc = 1.10 * steps.reshape(n_rounds, T * spb)
    return np.concatenate([np.full((n_rounds, 1), 1.10), 1.10 + np.cumsum(inc, axis=1)], axis=1)


# ---------- EA simulators (vectorised over lanes = rounds x 25 grid points) ----------

class Book:
    def __init__(self, n):
        self.bal = np.full(n, DEP)
        self.dead = np.zeros(n, bool)
        self.trades = np.zeros(n, int)
        self.wins = np.zeros(n, int)
        self.cost = np.zeros(n)
        self.stop_outs = np.zeros(n, int)
        self.maxpos = np.zeros(n, int)
        self.minml = np.full(n, np.inf)
        self.eq_close = []
        self.eq_min = []
        self.closed = [[] for _ in range(n)]  # closed P&L per basket / position (for S3)

    def close(self, mask, pnl, npos):
        idx = np.flatnonzero(mask)
        self.bal[idx] += pnl[idx]
        self.trades[idx] += npos[idx]
        self.wins[idx] += (pnl[idx] > 0) * npos[idx]
        for i in idx:
            self.closed[i].append(float(pnl[i]))


def run_trend(path, fast, slow, spb=SPB):
    """A1. Decisions at H4 bar ends on bar closes (MA cross), always in the market, 0.10 lot; marked every step."""
    R, S1 = path.shape
    P = len(fast)
    n = R * P
    rr = np.repeat(np.arange(R), P)
    fa, sl = np.tile(fast, R), np.tile(slow, R)
    closes = path[:, ::spb]                              # bar closes, index 0 = start
    cs = np.concatenate([np.zeros((R, 1)), np.cumsum(closes, axis=1)], axis=1)
    units = 0.10 * CONTRACT
    bk = Book(n)
    pos = np.zeros(n)
    entry = np.zeros(n)
    day_min = np.full(n, np.inf)
    steps_per_day = BARS_PER_DAY * spb
    for s in range(1, S1):
        px = path[rr, s]
        eq = bk.bal + pos * units * (px - entry)
        day_min = np.minimum(day_min, eq)
        if (pos != 0).any():
            bk.minml = np.minimum(bk.minml, np.where(pos != 0, eq / (units * px / LEV) * 100, np.inf))
        if s % spb == 0:
            k = s // spb                                  # bars completed
            ok = k >= sl
            maf = np.where(ok, (cs[rr, k + 1] - cs[rr, np.maximum(k + 1 - fa, 0)]) / fa, 0)
            mas = np.where(ok, (cs[rr, k + 1] - cs[rr, np.maximum(k + 1 - sl, 0)]) / sl, 0)
            new = np.where(ok, np.sign(maf - mas), 0)
            ch = new != pos
            bk.close(ch & (pos != 0), pos * units * (px - entry), np.ones(n, int))
            opn = ch & (new != 0)
            bk.bal[opn] -= SPREAD * units
            bk.cost[opn] += SPREAD * units
            entry = np.where(opn, px, entry)
            pos = np.where(ch, new, pos)
            bk.maxpos = np.maximum(bk.maxpos, (pos != 0).astype(int))
        if s % steps_per_day == 0:
            bk.eq_close.append(bk.bal + pos * units * (px - entry))
            bk.eq_min.append(day_min.copy())
            day_min[:] = np.inf
    return bk


def run_grid(path, g_pips, tp_pips, mult, base, max_levels, spb=SPB):
    """A2 / A3. First entry only at a new H4 bar, contrarian to the last bar; grid adds, basket TP and the stop-out
    are checked at every step and fill at the step price."""
    R, S1 = path.shape
    P = len(g_pips)
    n = R * P
    rr = np.repeat(np.arange(R), P)
    g, tp, m = np.tile(g_pips, R) * PIP, np.tile(tp_pips, R) * PIP, np.tile(mult, R)
    bk = Book(n)
    lv = np.zeros(n, int)
    d = np.zeros(n)
    last = np.zeros(n)
    sl_ = np.zeros(n)
    slp = np.zeros(n)
    day_min = np.full(n, np.inf)
    steps_per_day = BARS_PER_DAY * spb
    for s in range(0, S1):
        px = path[rr, s]
        if s % spb == 0 and s < S1 - 1:
            start = (lv == 0) & ~bk.dead
            if start.any():
                need = base * CONTRACT * px / LEV
                can = start & (bk.bal > need)
                prev = path[rr, s - spb] if s >= spb else px
                dirn = np.where(px >= prev, -1.0, 1.0)
                d = np.where(can, dirn, d)
                lv = np.where(can, 1, lv)
                last = np.where(can, px, last)
                sl_ = np.where(can, base, sl_)
                slp = np.where(can, base * px, slp)
                bk.bal[can] -= SPREAD * base * CONTRACT
                bk.cost[can] += SPREAD * base * CONTRACT
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
            bk.bal[hit] -= SPREAD * lot[hit] * CONTRACT
            bk.cost[hit] += SPREAD * lot[hit] * CONTRACT
        fl = np.where(act, d * CONTRACT * (sl_ * px - slp), 0.0)
        eq = bk.bal + fl
        margin = sl_ * CONTRACT * px / LEV
        ml = np.where(act & (margin > 0), eq / np.where(margin > 0, margin, 1) * 100, np.inf)
        bk.minml = np.minimum(bk.minml, ml)
        so = act & (ml <= SO)
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


def simulate(arch, path):
    a, b = GRID[arch]
    A, B = np.meshgrid(a, b, indexing="ij")
    A, B = A.ravel(), B.ravel()
    if arch == "A1":
        return run_trend(path, A.astype(int), B.astype(int))
    if arch == "A2":
        return run_grid(path, A, B, np.ones_like(A, float), 0.05, 10)
    return run_grid(path, B, np.full_like(A, 10.0), A, 0.01, 12)


# ---------- metrics and pipeline ----------

def lane_metrics(bk, i):
    eq = np.array([e[i] for e in bk.eq_close])
    mn = np.array([e[i] for e in bk.eq_min])
    rows = [{"date": f"d{k}", "equity_close": f"{v:.2f}"} for k, v in enumerate(eq)]
    m, na = {}, {}
    daily_metrics(rows, DEP, m, na)
    m.update({"bankrupt": bool(mn.min() <= 0), "stop_out_deals": int(bk.stop_outs[i]),
              "min_margin_level": None if not math.isfinite(bk.minml[i]) else float(bk.minml[i]),
              "max_positions": int(bk.maxpos[i]), "trades": int(bk.trades[i]), "net_profit": float(eq[-1] - DEP),
              "equity_max_dd_pct": float(_ddpct(eq, mn))})
    return m, np.diff(np.concatenate([[DEP], eq])) / DEP


def _ddpct(eq, mn):
    peak = np.maximum.accumulate(np.concatenate([[DEP], eq[:-1]]))
    return float(((peak - mn) / peak).max() * 100)


def key(i, j):
    return f"cd_{i}_{j}"


def pipeline(arch, D, V0, V1, F, rr_round, P):
    """One round: P4 -> P5 -> P7 on Discovery, P8a on V0 + V1, FUT year for passers."""
    prof, _ = load_profile(ROOT / "configs" / "strategy_profiles.yaml", PROFILE[arch])
    sh = (5, 5)
    scores = np.full(sh, np.nan)
    statuses = np.full(sh, "OK", dtype=object)
    passed = np.zeros(sh, bool)
    mets = {}
    for p in range(P):
        i = rr_round * P + p
        m, _ = lane_metrics(D, i)
        idx = np.unravel_index(p, sh)
        s, st, _ = grid_stats.score_of(m)
        scores[idx], statuses[idx] = s, st
        dec = p4.evaluate(m, prof, DAYS["D"] / 260)
        passed[idx] = dec.outcome == "PASS"
        mets[key(*idx)] = (m, i)
    res = grid_stats.compute(scores, statuses.astype(str), passed, AXES[arch])
    cells = [tuple(int(x) for x in c) for c in np.argwhere(passed)]
    out = {"p4": len(cells), "selected": 0, "passers": []}
    if not cells:
        return out
    index_of = {key(*c): c for c in cells}
    regions = [([key(*cc) for cc in r["members"]], key(*r["medoid"])) for r in res.regions]
    for members, med in regions:
        for x in members + [med]:
            index_of[x] = tuple(int(v) for v in x[3:].split("_"))
    extra = {key(*c): (mets[key(*c)][0].get("daily_sharpe"), mets[key(*c)][0]["equity_max_dd_pct"]) for c in cells}
    dec = core.decide(scores, statuses.astype(str), [key(*c) for c in cells], index_of,
                      {key(*c): res.per_cell[c]["region_status"] for c in cells}, regions, extra, 5, 1.5, 0.0)
    sel = dec.selection.selected
    out["selected"] = len(sel)
    if not sel:
        return out
    rng = np.random.default_rng([SEED, 7, rr_round])
    pv, info = {}, {}
    for cd in sel:
        i = mets[cd][1]
        m0, r0 = lane_metrics(V0, i)
        m1, r1 = lane_metrics(V1, i)
        x = np.concatenate([r0, r1])
        sd = x.std(ddof=1)
        lb = 1.0
        if sd > 0:
            sr, g3, g4 = x.mean() / sd, skew(x), kurtosis(x, fisher=False)
            v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
            if v > 0:
                lb = 1 - norm.cdf(sr * math.sqrt(len(x) - 1) / math.sqrt(v))
        pv[cd] = ev.evidence([r0, r1], 2000, rng)["p"] if lb <= ev.ALPHA else lb
        net = m0["net_profit"] + m1["net_profit"]
        lots_closed = (V0.cost[i] + V1.cost[i]) / (SPREAD * CONTRACT)  # lots opened ~ lots closed
        info[cd] = {"ruin": m0["bankrupt"] or m1["bankrupt"] or m0["stop_out_deals"] > 0 or m1["stop_out_deals"] > 0,
                    "net": net, "net_cost": net - PIP * CONTRACT * lots_closed, "lane": i}
    rej = ev.holm(pv)
    for cd in sel:
        x = info[cd]
        if rej[cd] and not x["ruin"] and x["net"] > 0 and x["net_cost"] > 0:
            i = x["lane"]
            out["passers"].append({"cd": cd, "p": pv[cd], "fut_stop_out": bool(F.stop_outs[i] > 0 or
                                                                               min(e[i] for e in F.eq_min) <= 0),
                                   "fut_net": float(F.eq_close[-1][i] - DEP)})
    return out


def cp(k, n):
    lo = float(beta.ppf(0.025, k, n - k + 1)) if k else 0.0
    hi = float(beta.ppf(0.975, k + 1, n - k)) if k < n else 1.0
    return lo, hi


def run(arch, kind, reps):
    ar = 0.0 if kind == "null" else (0.05 if arch == "A1" else -0.05)
    rng = np.random.default_rng([SEED, {"A1": 1, "A2": 2, "A3": 3}[arch], 0 if kind == "null" else 1])
    P = 25
    chunk = 50
    rows = []
    gross = []
    for start in range(0, reps, chunk):
        R = min(chunk, reps - start)
        sims = {k: simulate(arch, prices(R, DAYS[k], rng, ar)) for k in ("D", "V0", "V1", "F")}
        D = sims["D"]
        for r in range(R):
            lanes = slice(r * P, (r + 1) * P)
            gross.append(float(np.mean(D.eq_close[-1][lanes] - DEP + D.cost[lanes])))
            rows.append(pipeline(arch, D, sims["V0"], sims["V1"], sims["F"], r, P))
        if arch == "A3" and kind == "null" and start == 0:
            s3(sims)
    k = sum(1 for x in rows if x["passers"])
    lo, hi = cp(k, len(rows))
    g = np.array(gross)
    print(f"{arch} {kind}: rounds {len(rows)}; P4 passes per round {np.mean([x['p4'] for x in rows]):.2f}; "
          f"selected per round {np.mean([x['selected'] for x in rows]):.2f}; "
          f"rounds with >= 1 P8a pass {k} = {k / len(rows):.4f} [95% CI {lo:.4f}-{hi:.4f}]"
          + (f" -> {'OK' if k / len(rows) <= 0.05 else 'NOT MET'} (proposed criterion <= 5%)" if kind == "null"
             else " (power, recorded only)"))
    ps = [p for x in rows for p in x["passers"]]
    if ps:
        print(f"{arch} {kind}: passers {len(ps)}; of them stopped out / bankrupt in the next year "
              f"{sum(p['fut_stop_out'] for p in ps)} ({sum(p['fut_stop_out'] for p in ps) / len(ps):.3f}); "
              f"mean next-year net {np.mean([p['fut_net'] for p in ps]):.1f} USD")
    if kind == "null":
        se = g.std(ddof=1) / math.sqrt(len(g))
        print(f"{arch} S1: mean gross P&L per run {g.mean():.2f} USD (SE {se:.2f}) -> "
              f"{'OK' if abs(g.mean()) <= 3 * se else 'NOT MET'}")


def s3(sims):
    """Structural vs naive shuffle for martingale lanes that never stopped out in D + V0 + V1."""
    D, V0, V1, F = (sims[k] for k in ("D", "V0", "V1", "F"))
    rng = np.random.default_rng([SEED, 33])
    surv = [i for i in range(len(D.bal)) if D.stop_outs[i] == 0 and V0.stop_outs[i] == 0 and V1.stop_outs[i] == 0
            and len(D.closed[i]) + len(V0.closed[i]) + len(V1.closed[i]) >= 30]
    struct = np.mean([F.stop_outs[i] > 0 for i in surv]) if surv else float("nan")
    naive = []
    for i in surv:
        pnl = np.array(D.closed[i] + V0.closed[i] + V1.closed[i])
        per_year = len(pnl) / (sum(DAYS[k] for k in ("D", "V0", "V1")) / 260)
        n = max(1, int(round(per_year)))
        ruins = 0
        for _ in range(200):
            path = DEP + np.cumsum(rng.choice(pnl, n, replace=True))
            ruins += path.min() <= 0.5 * DEP
        naive.append(ruins / 200)
    print(f"A3 S3: lanes without any stop-out in D+V0+V1: {len(surv)} of {len(D.bal)}; P(stop-out next year): "
          f"structural (EA re-run) {struct:.3f} vs naive shuffle of its own closed P&L {np.mean(naive):.4f}")


def hand_cases():
    """Exact small cases for the run-2 path model, worked out by hand BEFORE running it (acceptance: equality to the
    cent). (Run-1 bar-model hand cases: see null_sim_run1_INVALID_2026-10-12.txt and git history.)"""
    ok = True
    # A1 (1 step per bar): fast 1, slow 2, closes 1.10, 1.11, 1.12, 1.11.
    # k counts completed bars; at k=2 (price 1.12) MA1 = 1.12 > MA2 = 1.115 -> long 0.10 at 1.12 (spread
    # 1.00); at k=3 (1.11) MA1 1.11 < MA2 1.115 -> close long at 1.11 (-100.00), open short (spread 1.00).
    # Balance 10,000 - 1 - 100 - 1 = 9,898.00, 1 trade.
    path = np.array([[1.10, 1.11, 1.12, 1.11]])
    bk = run_trend(path, np.array([1]), np.array([2]), spb=1)
    ok &= round(bk.bal[0], 2) == 9898.00 and bk.trades[0] == 1
    print(f"hand A1: balance {bk.bal[0]:.2f} (expected 9898.00), trades {bk.trades[0]} (expected 1)")
    # A3 (8 steps per bar): m 2, base 0.01, grid 10, tp 10. Step 0 (new bar, no history) opens SHORT 0.01 at 1.1000
    # (spread 0.10). Step 1 at 1.1012 >= 1.1010 adds 0.02 at 1.1012 (spread 0.20); average 1.1008, target 1.0998.
    # Step 2 at 1.0997 <= 1.0998 closes: gross = 100,000 * (0.011 + 0.022024 - 0.03 * 1.0997) = 3.30.
    # Balance 10,000 - 0.30 + 3.30 = 10,003.00, 2 trades, cost 0.30.
    path = np.array([[1.1000, 1.1012, 1.0997, 1.0999]])
    bk = run_grid(path, np.array([10.0]), np.array([10.0]), np.array([2.0]), 0.01, 12)
    ok &= round(bk.bal[0], 2) == 10003.00 and bk.trades[0] == 2 and round(bk.cost[0], 2) == 0.30
    print(f"hand A3: balance {bk.bal[0]:.2f} (expected 10003.00), trades {bk.trades[0]} (2), cost {bk.cost[0]:.2f} (0.30)")
    # A2: constant 0.05: add 0.05 at 1.1012 (spread 0.50 each), average 1.1006, target 1.0996; step 2 at 1.0997 does
    # not reach it; step 3 at 1.0995 closes: gross = 100,000 * (0.055 + 0.05506 - 0.10 * 1.0995) = 11.00.
    # Balance 10,000 - 1.00 + 11.00 = 10,010.00, 2 trades, cost 1.00.
    path = np.array([[1.1000, 1.1012, 1.0997, 1.0995]])
    bk = run_grid(path, np.array([10.0]), np.array([10.0]), np.array([1.0]), 0.05, 10)
    ok &= round(bk.bal[0], 2) == 10010.00 and bk.trades[0] == 2 and round(bk.cost[0], 2) == 1.00
    print(f"hand A2: balance {bk.bal[0]:.2f} (expected 10010.00), trades {bk.trades[0]} (2), cost {bk.cost[0]:.2f} (1.00)")
    # stop-out: short 1.0 lot at 1.1000 (spread 10.00), grid 1,000 pips. Step 1 at 1.1900: equity 990, margin
    # 1,190 -> 83%. Step 2 at 1.2000: equity -10 -> stop-out at 1.2000. Balance -10.00, 1 stop-out.
    path = np.array([[1.1000, 1.1900, 1.2000]])
    bk = run_grid(path, np.array([1000.0]), np.array([10.0]), np.array([2.0]), 1.0, 12)
    ok &= bk.stop_outs[0] == 1 and round(bk.bal[0], 2) == -10.00
    print(f"hand stop-out: stop-outs {bk.stop_outs[0]} (1), balance {bk.bal[0]:.2f} (expected -10.00)")
    print("hand cases:", "OK" if ok else "NOT MET")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        parts = arg.split(":")
        if parts[0] == "hand":
            hand_cases()
        elif parts[0] == "run":
            run(parts[1], parts[2], int(parts[3]) if len(parts) > 3 else (400 if parts[2] == "null" else 200))
