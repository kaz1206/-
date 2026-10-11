"""P8 plan §4.4: validity of the per-period p-value (H1), the Holm implementation (H2), family-wise error with
Holm (H3) and power (H4). Criteria were committed before running (P8_PLAN §4.4, commit 82ebd94).

V0 (516 days) and V1 (115 days) are generated SEPARATELY and aggregated per P8_PLAN §4.2.1: no return across
the boundary, no zero returns for the gap, autocorrelation only from pairs inside a period, stationary bootstrap
per period (blocks never cross the boundary). p = 1 - min(PSR standard, AR(1) n*, Newey-West n*, bootstrap SE).
Seed 20261011; bootstrap SE with B = 200 per repetition.

Usage: uv run python docs/research/p8_simulations/validation_gate2.py <case> ...
  cases: h1:<kind>  h2  h3:<kind>:<rho>  h4:<kind>:<k>:<mu>
"""
import math
import sys

import numpy as np
from arch.bootstrap import StationaryBootstrap, optimal_block_length
from scipy.signal import lfilter
from scipy.stats import beta, kurtosis, norm, skew
from statsmodels.stats.multitest import multipletests

N0, N1, SEED, ALPHA, B = 516, 115, 20261011, 0.05, 200
LEVELS = (0.01, 0.025, 0.05, 0.10)


def cp(k, n):
    lo = beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return round(float(lo), 5), round(float(hi), 5)


# ----- data: one period, k candidates -----

def period(kind, rng, n, k, rho, scale=1.0):
    z = math.sqrt(rho) * rng.standard_normal((n, 1)) + math.sqrt(1 - rho) * rng.standard_normal((n, k))
    if kind in ("iid", "vol2"):
        x = z
    elif kind.startswith("ar1_"):
        x = lfilter([1], [1, -float(kind[4:])], z, axis=0)
    elif kind == "ar2":
        x = lfilter([1], [1, -0.3, -0.3], z, axis=0)
    elif kind == "ma5":
        x = lfilter(np.ones(6) / np.sqrt(6), [1], z, axis=0)
    elif kind == "garch_ar1":
        h = np.ones((n, 1))  # the GARCH state starts afresh in every period
        u = np.empty_like(z)
        for t in range(n):
            u[t] = np.sqrt(h[t]) * z[t]
            if t + 1 < n:
                h[t + 1] = 0.05 + 0.1 * u[t].mean() ** 2 + 0.85 * h[t]
        x = lfilter([1], [1, -0.4], u, axis=0)
    else:
        raise ValueError(kind)
    return x * scale


def gen(kind, rng, k, rho, mus):
    """Two separate periods; a true daily Sharpe mu is added relative to each column's population sd."""
    x0 = period(kind, rng, N0, k, rho)
    x1 = period(kind, rng, N1, k, rho, 2.0 if kind == "vol2" else 1.0)
    mus = np.asarray(mus, dtype=float)
    if mus.any():
        x0 = x0 + mus * x0.std(axis=0, ddof=1)
        x1 = x1 + mus * x1.std(axis=0, ddof=1)
    return x0, x1


# ----- the per-period p-value (P8_PLAN §4.2 / §4.2.1) -----

def acf_segments(segs, k):
    allx = np.concatenate(segs)
    m = allx.mean()
    den = float(((allx - m) ** 2).sum())
    if den == 0:
        return None
    num = sum(float(((s[k:] - m) * (s[:-k] - m)).sum()) for s in segs if len(s) > k)
    return num / den


def psr_n(sr, n_eff, g3, g4):
    v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    if not (v > 0) or n_eff < 2:
        return None
    return float(norm.cdf(sr * math.sqrt(n_eff - 1) / math.sqrt(v)))


def boot_se(segs, rng, reps=B):
    gens = []
    for s in segs:
        b = max(1.0, float(optimal_block_length(s)["stationary"].iloc[0]))
        gens.append(StationaryBootstrap(b, s, seed=rng).bootstrap(reps))
    out = []
    for draws in zip(*gens, strict=True):
        z = np.concatenate([d[0][0] for d in draws])
        sd = z.std(ddof=1)
        if sd > 0:
            out.append(z.mean() / sd)
    return float(np.std(out, ddof=1)) if len(out) >= 2 else None


def p_value(segs, rng):
    x = np.concatenate(segs)
    n = len(x)
    sd = x.std(ddof=1)
    if n < 30 or sd == 0:
        return 1.0
    sr, g3, g4 = float(x.mean() / sd), float(skew(x)), float(kurtosis(x, fisher=False))
    std = psr_n(sr, n, g3, g4)
    if std is None:
        return 1.0
    vals = [std]
    r1 = acf_segments(segs, 1)
    n_ar1 = n * (1 - r1) / (1 + r1) if r1 is not None and r1 > 0 else n
    lag = int(math.floor(4 * (n / 100) ** (2 / 9)))
    rs = [acf_segments(segs, k) for k in range(1, lag + 1)]
    f = 1 + 2 * sum((1 - k / (lag + 1)) * r for k, r in enumerate(rs, start=1)) if None not in rs else None
    n_nw = n / f if f is not None and f > 1 else n
    for ne in (n_ar1, n_nw):
        v = psr_n(sr, ne, g3, g4)
        if v is not None:
            vals.append(v)
    se = boot_se(segs, rng)
    if se and se > 0:
        vals.append(float(norm.cdf(sr / se)))
    return 1.0 - min(vals)


def holm(ps, alpha=ALPHA, ids=None):
    """Own Holm step-down (P8_PLAN §4.2): ascending p (ties by id), stop at the first non-rejection."""
    ids = list(range(len(ps))) if ids is None else ids
    order = sorted(range(len(ps)), key=lambda i: (ps[i], ids[i]))
    rej = [False] * len(ps)
    m = len(ps)
    for rank, i in enumerate(order):
        if ps[i] <= alpha / (m - rank):
            rej[i] = True
        else:
            break
    return rej


# ----- cases -----

def h1(kind, reps=10_000):
    rng = np.random.default_rng(SEED)
    hits = dict.fromkeys(LEVELS, 0)
    for _ in range(reps):
        x0, x1 = gen(kind, rng, 1, 0.0, [0.0])
        p = p_value([x0[:, 0], x1[:, 0]], rng)
        for u in LEVELS:
            hits[u] += p <= u
    parts = []
    for u in LEVELS:
        lo, hi = cp(hits[u], reps)
        ok = "OK" if hi <= 1.4 * u else "OVER"
        parts.append(f"u={u}: {hits[u] / reps:.4f} ({lo}, {hi}) limit {1.4 * u:.3f} {ok}")
    print(f"H1 {kind:10s} reps={reps} | " + " | ".join(parts), flush=True)


def h2(cases=10_000):
    rng = np.random.default_rng(SEED)
    mism = 0
    grid = np.array([0.0, 0.001, 0.0125, 0.01, 0.0166666, 0.025, 0.05, 0.0500001, 0.2, 1.0])
    for c in range(cases):
        k = int(rng.integers(1, 9))
        ps = np.where(rng.random(k) < 0.4, rng.choice(grid, k), rng.random(k) * 0.1)
        ours = holm(list(ps))
        ref = multipletests(ps, alpha=ALPHA, method="holm")[0]
        mism += list(ref) != ours
    hand = [(0.01, 0.04, 0.03, 0.005), (0.012, 0.013, 0.02, 0.3)]
    # hand: 0.005<=0.0125 yes, 0.01<=0.0167 yes, 0.03<=0.025 no -> stop; second: 0.012<=0.0125 yes, 0.013<=0.0167 yes,
    # 0.02<=0.025 yes, 0.3<=0.05 no
    want = [[True, False, False, True], [True, True, True, False]]
    hand_ok = all(holm(list(h)) == w for h, w in zip(hand, want, strict=True))
    print(f"H2 holm vs statsmodels: {cases} cases, mismatches={mism}; hand examples {'OK' if hand_ok else 'FAIL'}",
          flush=True)


def family(kind, k, rho, mus, reps=2000):
    rng = np.random.default_rng(SEED)
    null_any = edge = 0
    nulls = np.asarray(mus) == 0
    for _ in range(reps):
        x0, x1 = gen(kind, rng, k, rho, mus)
        ps = [p_value([x0[:, j], x1[:, j]], rng) for j in range(k)]
        rej = np.array(holm(ps))
        null_any += bool(rej[nulls].any())
        edge += bool(rej[~nulls].any()) if (~nulls).any() else 0
    return null_any, edge, reps


def h3(kind, rho):
    a, _, r = family(kind, 5, float(rho), [0.0] * 5)
    lo, hi = cp(a, r)
    print(f"H3 K=5 {kind:10s} corr {rho}: FWER {a / r:.4f} ({lo}, {hi}) {'OK' if hi <= 0.07 else 'OVER'}", flush=True)


def h4(kind, k, mu):
    k, mu = int(k), float(mu)
    mus = [mu] + [0.0] * (k - 1)
    a, e, r = family(kind, k, 0.5 if k > 1 else 0.0, mus)
    print(f"H4 K={k} {kind:10s} edge {mu}: power {e / r:.4f} {cp(e, r)}  (null FWER {a / r:.4f})", flush=True)


if __name__ == "__main__":
    for case in sys.argv[1:]:
        name, *args = case.split(":")
        {"h1": h1, "h2": h2, "h3": h3, "h4": h4}[name](*args)
