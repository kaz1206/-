"""P9 plan, pre-implementation simulation of the risk engine (drafted 2026-10-11). Seed 20261011.

Prototype only (numpy + scipy, no robustlab): the production code is re-checked with the same conditions and seeds
after implementation.

Model (P9_PLAN §3): fixed-lot backtests give, per day and PER LOT, the close-to-close P&L x_t, the intraday dip below
the lower of the two closes d_t <= 0, the number of closing deals c_t and whether a position was open pos_t. Amounts
are in units of the starting equity E0. With k lots for the whole horizon (no compounding, ARCHITECTURE §12.1):
  close equity        1 + k*C_t,  C_t = x_1 + ... + x_t
  intraday low        1 + k*M_t,  M_t = C_{t-1} + min(0, x_t) + d_t
  drawdown (from the close peak)   k*(P_t - M_t) / (1 + k*P_t),  P_t = max(0, C_1..C_{t-1})
  ruin                1 + k*M_t <= 1 - RUIN          (equity lost half of E0)
  stop-out            1 + k*M_t <= SO * k * m * pos_t   (margin level <= SO, m = margin per lot)
All three are monotone in k on a fixed path, so each path has a critical k (the smallest k that breaks the limit) and
P(limit broken at k) = share of paths whose critical k <= k. The allowed k for "P <= p" uses the one-sided 95% upper
Clopper-Pearson bound of the share (Monte Carlo error is charged against the limit, never in favour of it).

Scenarios (each from the history; segments = Discovery / V0 / V1, never crossing a boundary):
  S0 stationary bootstrap per segment (Politis-White block length)
  S1 trade-order shuffle (iid resampling of trades, closed P&L only; the classic Monte Carlo)
  S2 losing streak: S0 + a run of ceil(1.5 * longest observed losing-day run) losing days (drawn from the observed
     losing days) inserted at a random place
  S3 autocorrelation: S0 with an AR(1) rho = 0.3 filter on the demeaned P&L (variance kept, long-run variance x1.86)
  S4 volatility shock: S0 with a 63-day window where x -> mu + 2*(x - mu) and d -> 2*d
  S5 spread widening: S0 minus 2 x the cost-stress width per closing deal
  S6 no edge: S0 with the mean removed (the selected candidate's mean is biased upwards: winner's curse, P8b C4)
  SC combined: S6 + S3 + S4 + S2 + S5 together
Constraints (proposed defaults, P9_PLAN §4): P(DD > 20%) <= 5% in EACH of S0..S6; P(ruin) <= 1% and
P(stop-out) <= 1% in every scenario including SC. k_safe = the largest k that satisfies all of them.

Checks (acceptance written here BEFORE running):
 R1 engine
   R1a the critical-k shortcut equals a brute-force recomputation of DD / ruin / stop-out on a grid of k
       (60 random cases x 40 k values): identical counts.
   R1b ruin probability of iid normal days vs the Brownian closed form with the Broadie-Glasserman continuity
       correction (barrier shifted by 0.5826*sigma): |difference| <= 4 MC standard errors + 0.003 in all 6 settings.
 R2 coverage (the truth): history 500 + 500 + 115 days from a known process; k_safe from the procedure above
    (4,000 paths per scenario); then the TRUE probability at k_safe from 20,000 fresh 252-day paths of the same
    process. 200 repetitions per process.
    Acceptance for D1..D6: true P(DD > 20%) <= 5% in >= 90% of repetitions AND true P(ruin) <= 1% in >= 95%.
      D1 iid normal, true daily Sharpe 0.08     D2 Student t(4), Sharpe 0.08     D3 GARCH(1,1) 0.05/0.90, 0.08
      D4 AR(1) 0.2, 0.08                        D5 future volatility x1.5 for the whole horizon, 0.08
      D6 no edge, history kept only if it would pass a one-sided t-test at 5% (selection, winner's curse)
    Recorded only: D7 AR(2) (0.2, 0.15) (known limitation of P8a), and the same with S0 only (no stress) to show
    what the stress scenarios add; k_safe / k_oracle (k_oracle = the k with true P(DD > 20%) = 5%) and the binding
    scenario.
Usage: uv run python docs/research/p9_simulations/risk_sim.py r1 | r2:<D1..D7>
"""
import math
import sys

import numpy as np
from scipy.stats import beta, norm, t as student_t

SEED = 20261011
H = 252
L, ALPHA = 0.20, 0.05
RUIN, P_RUIN = 0.50, 0.01
SO, P_SO = 0.50, 0.01
SEGS = (500, 500, 115)
SIGMA = 0.005           # per-lot daily sd in units of E0 (k = 1 lot -> 0.5% of E0 per day)
MARGIN = 0.02           # margin per lot in units of E0
WIDTH = 0.05 * SIGMA    # cost-stress width per lot per closing deal
N_PATHS, N_TRUE = 4000, 20000
REPS = int(__import__("os").environ.get("P9_REPS", "200"))


# ---------- critical k ----------

def lows(x, d):
    c = np.cumsum(x, axis=1)
    prev = np.concatenate([np.zeros((x.shape[0], 1)), c[:, :-1]], axis=1)
    m = prev + np.minimum(0.0, x) + d
    peak = np.maximum.accumulate(np.maximum(prev, 0.0), axis=1)
    return m, peak


def crit_dd(x, d, lim=L):
    m, p = lows(x, d)
    den = p * (1 - lim) - m
    with np.errstate(divide="ignore"):
        k = np.where(den > 0, lim / np.where(den > 0, den, 1.0), np.inf)
    return k.min(axis=1)


def crit_ruin(x, d, ruin=RUIN):
    m, _ = lows(x, d)
    worst = (-m).max(axis=1)
    with np.errstate(divide="ignore"):
        return np.where(worst > 0, ruin / np.where(worst > 0, worst, 1.0), np.inf)


def crit_so(x, d, pos, so=SO, margin=MARGIN):
    m, _ = lows(x, d)
    worst = (so * margin * pos - m).max(axis=1)
    with np.errstate(divide="ignore"):
        return np.where(worst > 0, 1.0 / np.where(worst > 0, worst, 1.0), np.inf)


def c_max(n, p, conf=0.95):
    """Largest count whose one-sided upper Clopper-Pearson bound is <= p."""
    c = int(n * p)
    while c >= 0 and beta.ppf(conf, c + 1, n - c) > p:
        c -= 1
    return c


def allowed(crit, p):
    s = np.sort(crit)
    c = c_max(len(s), p)
    return float(s[c]) if c >= 0 else 0.0  # any k below the (c+1)-th smallest critical k breaks <= c paths


# ---------- history and scenarios ----------

def block_length(x):
    from arch.bootstrap import optimal_block_length
    return max(1.0, float(optimal_block_length(x)["stationary"].iloc[0]))


def sb_index(seg_lens, b, n_paths, rng):
    ends = np.cumsum(seg_lens)
    starts = ends - np.array(seg_lens)
    seg_end_of = np.repeat(ends, seg_lens)
    n = int(ends[-1])
    idx = np.empty((n_paths, H), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_paths)
    for t in range(1, H):
        nxt = idx[:, t - 1] + 1
        jump = (rng.random(n_paths) < 1.0 / b) | (nxt >= seg_end_of[idx[:, t - 1]])
        idx[:, t] = np.where(jump, rng.integers(0, n, n_paths), nxt)
    del starts
    return idx


def longest_losing_run(x, seg_lens):
    best, pos = 0, 0
    for n in seg_lens:
        run = 0
        for v in x[pos:pos + n]:
            run = run + 1 if v < 0 else 0
            best = max(best, run)
        pos += n
    return best


def scenarios(hist, rng, n_paths=N_PATHS, only_s0=False):
    x, d, c, pos, seg_lens = hist["x"], hist["d"], hist["c"], hist["pos"], hist["seg_lens"]
    mu = float(x.mean())
    b = np.mean([block_length(x[s - n:s]) for s, n in zip(np.cumsum(seg_lens), seg_lens)])
    idx = sb_index(seg_lens, b, n_paths, rng)
    X, D, C, P = x[idx], d[idx], c[idx], pos[idx]
    out = {"S0": (X, D, P)}
    if only_s0:
        return out, b
    j = rng.integers(0, len(x), (n_paths, H))
    out["S1"] = (x[j], np.zeros_like(X), pos[j])

    def streak(X, D, P):
        X, D, P = X.copy(), D.copy(), P.copy()
        k = math.ceil(1.5 * longest_losing_run(x, seg_lens))
        losers = np.flatnonzero(x < 0)
        st = rng.integers(0, H - k + 1, n_paths)
        pick = losers[rng.integers(0, len(losers), (n_paths, k))]
        rows = np.arange(n_paths)[:, None]
        cols = st[:, None] + np.arange(k)[None, :]
        X[rows, cols], D[rows, cols], P[rows, cols] = x[pick], d[pick], pos[pick]
        return X, D, P

    def ar(X, center, rho=0.3):
        z = X - center
        e = np.empty_like(z)
        e[:, 0] = z[:, 0]
        s = math.sqrt(1 - rho * rho)
        for t in range(1, H):
            e[:, t] = rho * e[:, t - 1] + s * z[:, t]
        return center + e

    def shock(X, D, center, w=63, f=2.0):
        X, D = X.copy(), D.copy()
        st = rng.integers(0, H - w + 1, n_paths)
        mask = (np.arange(H)[None, :] >= st[:, None]) & (np.arange(H)[None, :] < st[:, None] + w)
        X = np.where(mask, center + f * (X - center), X)
        D = np.where(mask, f * D, D)
        return X, D

    out["S2"] = streak(X, D, P)
    out["S3"] = (ar(X, mu), D, P)
    out["S4"] = (*shock(X, D, mu), P)
    out["S5"] = (X - 2 * WIDTH * C, D, P)
    out["S6"] = (X - mu, D, P)
    Z = ar(X - mu, 0.0)                    # no edge, then autocorrelation around 0
    Z, Dc = shock(Z, D, 0.0)               # volatility shock around 0
    Z, Dc, Pc = streak(Z, Dc, P)           # losing streak (observed losing days, not demeaned)
    out["SC"] = (Z - 2 * WIDTH * C, Dc, Pc)  # spread widening on the original closing deals
    return out, b


def k_safe(sc):
    lim = {}
    for name, (X, D, P) in sc.items():
        if name != "SC":
            lim[(name, "dd")] = allowed(crit_dd(X, D), ALPHA)
        lim[(name, "ruin")] = allowed(crit_ruin(X, D), P_RUIN)
        lim[(name, "stop_out")] = allowed(crit_so(X, D, P), P_SO)
    key = min(lim, key=lim.get)
    return lim[key], key, lim


# ---------- data-generating processes ----------

def gen(dgp, n, rng, future=False):
    sr = 0.0 if dgp == "D6" else 0.08
    if dgp == "D2":
        z = student_t.rvs(4, size=n, random_state=rng) / math.sqrt(2.0)
    elif dgp == "D3":
        a, bb = 0.05, 0.90
        om = 1 - a - bb
        z = np.empty(n)
        h, e = 1.0, 0.0
        for i in range(n):
            h = om + a * e * e + bb * h
            e = math.sqrt(h) * rng.standard_normal()
            z[i] = e
    elif dgp == "D4":
        r = 0.2
        z = np.empty(n)
        z[0] = rng.standard_normal()
        for i in range(1, n):
            z[i] = r * z[i - 1] + math.sqrt(1 - r * r) * rng.standard_normal()
    elif dgp == "D7":
        p1, p2 = 0.2, 0.15
        z = np.zeros(n + 200)
        e = rng.standard_normal(n + 200)
        for i in range(2, n + 200):
            z[i] = p1 * z[i - 1] + p2 * z[i - 2] + e[i]
        z = z[200:]
        g0 = (1 - p2) / ((1 + p2) * ((1 - p2) ** 2 - p1 ** 2))
        z = z / math.sqrt(g0)
    else:
        z = rng.standard_normal(n)
    vol = SIGMA * (1.5 if (dgp == "D5" and future) else 1.0)
    x = sr * SIGMA + vol * z
    d = -np.abs(rng.normal(0, 0.5 * vol, n))
    c = rng.poisson(0.5, n).astype(float)
    pos = (rng.random(n) < 0.7).astype(float)
    return x, d, c, pos


def gen_future(dgp, n_paths, rng):
    xs, ds, ps = [], [], []
    for _ in range(n_paths):
        x, d, c, p = gen(dgp, H, rng, future=True)
        xs.append(x); ds.append(d); ps.append(p)
    return np.array(xs), np.array(ds), np.array(ps)


def gen_future_fast(dgp, n_paths, rng):
    if dgp in ("D1", "D5", "D6"):
        vol = SIGMA * (1.5 if dgp == "D5" else 1.0)
        sr = 0.0 if dgp == "D6" else 0.08
        x = sr * SIGMA + vol * rng.standard_normal((n_paths, H))
        d = -np.abs(rng.normal(0, 0.5 * vol, (n_paths, H)))
        p = (rng.random((n_paths, H)) < 0.7).astype(float)
        return x, d, p
    if dgp == "D2":
        z = student_t.rvs(4, size=(n_paths, H), random_state=rng) / math.sqrt(2.0)
    elif dgp == "D3":
        a, bb = 0.05, 0.90
        om = 1 - a - bb
        z = np.empty((n_paths, H))
        h = np.ones(n_paths)
        e = np.zeros(n_paths)
        for i in range(H):
            h = om + a * e * e + bb * h
            e = np.sqrt(h) * rng.standard_normal(n_paths)
            z[:, i] = e
    elif dgp == "D4":
        r = 0.2
        z = np.empty((n_paths, H))
        z[:, 0] = rng.standard_normal(n_paths)
        for i in range(1, H):
            z[:, i] = r * z[:, i - 1] + math.sqrt(1 - r * r) * rng.standard_normal(n_paths)
    elif dgp == "D7":
        p1, p2 = 0.2, 0.15
        g0 = (1 - p2) / ((1 + p2) * ((1 - p2) ** 2 - p1 ** 2))
        zz = np.zeros((n_paths, H + 200))
        e = rng.standard_normal((n_paths, H + 200))
        for i in range(2, H + 200):
            zz[:, i] = p1 * zz[:, i - 1] + p2 * zz[:, i - 2] + e[:, i]
        z = zz[:, 200:] / math.sqrt(g0)
    x = 0.08 * SIGMA + SIGMA * z
    d = -np.abs(rng.normal(0, 0.5 * SIGMA, (n_paths, H)))
    p = (rng.random((n_paths, H)) < 0.7).astype(float)
    return x, d, p


def history(dgp, rng):
    while True:
        parts = [gen(dgp, n, rng) for n in SEGS]
        x = np.concatenate([p[0] for p in parts])
        if dgp != "D6":
            break
        tstat = x.mean() / x.std(ddof=1) * math.sqrt(len(x))
        if tstat > norm.ppf(0.95):
            break
    return {"x": x, "d": np.concatenate([p[1] for p in parts]), "c": np.concatenate([p[2] for p in parts]),
            "pos": np.concatenate([p[3] for p in parts]), "seg_lens": list(SEGS)}


# ---------- checks ----------

def brute(X, D, P, k):
    e_close = 1 + k * np.cumsum(X, axis=1)
    prev = np.concatenate([np.ones((X.shape[0], 1)), e_close[:, :-1]], axis=1)
    low = prev + k * (np.minimum(0, X) + D)
    peak = np.maximum.accumulate(prev, axis=1)
    peak = np.maximum(peak, 1.0)
    dd = ((peak - low) / peak).max(axis=1) >= L
    ruin = (low <= 1 - RUIN).any(axis=1)
    so = (low <= SO * k * MARGIN * P).any(axis=1)
    return int(dd.sum()), int(ruin.sum()), int(so.sum())


def r1():
    rng = np.random.default_rng(SEED)
    bad = 0
    for case in range(60):
        n = 300
        X = rng.normal(rng.uniform(-0.1, 0.2) * SIGMA, SIGMA, (n, H))
        D = -np.abs(rng.normal(0, 0.5 * SIGMA, (n, H)))
        P = (rng.random((n, H)) < 0.7).astype(float)
        cd, cr, cs = crit_dd(X, D), crit_ruin(X, D), crit_so(X, D, P)
        for k in np.linspace(0.2, 40, 40):
            a = (int((cd <= k).sum()), int((cr <= k).sum()), int((cs <= k).sum()))
            bb = brute(X, D, P, k)
            if a != bb:
                bad += 1
                if bad <= 5:
                    print("R1a mismatch case", case, "k", k, a, bb)
    print(f"R1a: mismatches {bad} of {60 * 40} -> {'OK' if bad == 0 else 'NOT MET'}")
    ok = True
    for mu_sr, k in ((0.0, 15), (0.0, 25), (0.05, 20), (0.05, 30), (0.10, 30), (-0.03, 15)):
        n = 40000
        X = rng.normal(mu_sr * SIGMA, SIGMA, (n, H))
        cr = crit_ruin(X, np.zeros_like(X))
        est = float((cr <= k).mean())
        mu, s, a, T = k * mu_sr * SIGMA, k * SIGMA, RUIN, H
        a_bg = a + 0.5826 * s
        an = norm.cdf((-a_bg - mu * T) / (s * math.sqrt(T))) + \
            math.exp(-2 * mu * a_bg / s ** 2) * norm.cdf((-a_bg + mu * T) / (s * math.sqrt(T)))
        se = math.sqrt(max(an * (1 - an), 1e-12) / n)
        good = abs(est - an) <= 4 * se + 0.003
        ok &= good
        print(f"R1b sr={mu_sr:+.2f} k={k}: simulated {est:.4f}, closed form {an:.4f}, se {se:.4f} -> "
              f"{'OK' if good else 'NOT MET'}")
    print("R1b:", "OK" if ok else "NOT MET")
    # R1c, ADDED AFTER the first run of R1b: those six settings were all in the 45-80% region, far from the 1% limit
    # actually used. Settings with ruin probabilities around 0.1-5%; tolerance 4 MC standard errors + 0.0005.
    ok = True
    for mu_sr, k in ((0.0, 2.0), (0.0, 2.5), (0.0, 3.0), (0.05, 3.0), (0.05, 4.0), (-0.03, 2.5)):
        n = 200000
        X = rng.normal(mu_sr * SIGMA, SIGMA, (n, H))
        cr = crit_ruin(X, np.zeros_like(X))
        est = float((cr <= k).mean())
        mu, s, a, T = k * mu_sr * SIGMA, k * SIGMA, RUIN, H
        a_bg = a + 0.5826 * s
        an = norm.cdf((-a_bg - mu * T) / (s * math.sqrt(T))) + \
            math.exp(-2 * mu * a_bg / s ** 2) * norm.cdf((-a_bg + mu * T) / (s * math.sqrt(T)))
        se = math.sqrt(max(an * (1 - an), 1e-12) / n)
        good = abs(est - an) <= 4 * se + 0.0005
        ok &= good
        print(f"R1c sr={mu_sr:+.2f} k={k}: simulated {est:.5f}, closed form {an:.5f}, se {se:.5f} -> "
              f"{'OK' if good else 'NOT MET'}")
    print("R1c:", "OK" if ok else "NOT MET")


def r2(dgp):
    rng = np.random.default_rng([SEED, int(dgp[1:])])
    rows = []
    for rep in range(REPS):
        h = history(dgp, rng)
        sc, b = scenarios(h, rng)
        ks, key, lim = k_safe(sc)
        s0, _ = scenarios(h, rng, only_s0=True)
        k0, key0, _ = k_safe(s0)
        Xf, Df, Pf = gen_future_fast(dgp, N_TRUE, rng)
        tdd, trn = crit_dd(Xf, Df), crit_ruin(Xf, Df)
        k_or = float(np.quantile(tdd, ALPHA))
        rows.append((float((tdd <= ks).mean()), float((trn <= ks).mean()), float((tdd <= k0).mean()),
                     float((trn <= k0).mean()), ks / k_or, k0 / k_or, f"{key[0]}:{key[1]}", b))
    a = np.array([r[:6] for r in rows])
    keys = {}
    for r in rows:
        keys[r[6]] = keys.get(r[6], 0) + 1
    ok_dd, ok_r = float((a[:, 0] <= ALPHA).mean()), float((a[:, 1] <= P_RUIN).mean())
    met = ok_dd >= 0.90 and ok_r >= 0.95
    print(f"R2 {dgp}: full: share(true P(DD>20%) <= 5%) = {ok_dd:.3f}, median true P(DD>20%) = "
          f"{np.median(a[:, 0]):.4f}, 90th pct {np.quantile(a[:, 0], 0.9):.4f}; share(true P(ruin) <= 1%) = "
          f"{ok_r:.3f}; k_safe/k_oracle median {np.median(a[:, 4]):.3f} [10%-90%: {np.quantile(a[:, 4], 0.1):.3f}"
          f"-{np.quantile(a[:, 4], 0.9):.3f}] -> {'OK' if met else 'NOT MET'}{' (recorded only)' if dgp == 'D7' else ''}")
    print(f"R2 {dgp}: S0 only: share(true P(DD>20%) <= 5%) = {float((a[:, 2] <= ALPHA).mean()):.3f}, median "
          f"{np.median(a[:, 2]):.4f}, 90th pct {np.quantile(a[:, 2], 0.9):.4f}; share(ruin ok) = "
          f"{float((a[:, 3] <= P_RUIN).mean()):.3f}; k_S0/k_oracle median {np.median(a[:, 5]):.3f}")
    print(f"R2 {dgp}: binding {dict(sorted(keys.items(), key=lambda kv: -kv[1]))}; mean block length "
          f"{np.mean([r[7] for r in rows]):.2f}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        if arg == "r1":
            r1()
        elif arg.startswith("r2:"):
            r2(arg.split(":")[1])
