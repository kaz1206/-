"""P9 plan draft 2, two checks requested on 2026-10-11 before implementation. Seed 20261011.
Uses the prototype engine of risk_sim.py; the P8a evidence p-value and Holm come from the PRODUCTION P8a code
(robustlab.stats.validation.evidence / holm, the functions that actually decide P8a).

Acceptance written here BEFORE running.

 R3 (request 1: trade-order shuffle without floating loss)
   Scenario set of draft 2: S1 is split.
     S1a classic trade shuffle: closed P&L of trades resampled iid, equity seen only at trade closes, no floating loss.
         REFERENCE ONLY (reported next to the others as the standard method, never used for a limit), because the
         same 20% DD threshold measured on closed equity is not the same quantity as on intraday equity.
     S1b daily shuffle: days resampled iid WITH their intraday dips d_t (order randomised, floating loss kept).
         Used for the limits in place of the old S1.
   R3a (recorded): for D1, D3, D4, D7 (200 repetitions), the DD-allowed lot of S1a / S1b / S0, the ratio
       k_S1a / k_S1b (> 1 means S1a understates the risk), and the same for S1a built from 3-day trades
       (equity seen every 3 days only; closer to a real EA whose trades last several days).
   R3b (acceptance): R2 repeated for D1..D6 with the draft-2 scenario set (S1b in the limits, S1a reference):
       true P(DD > 20%) <= 5% in >= 90% of repetitions AND true P(ruin) <= 1% in >= 95% (D5 is expected to stay
       not met; it is reported, not re-tuned).

 R4 (request 2: selection as in the real pipeline, no edge anywhere)
   History of the selected candidate, true daily Sharpe 0 everywhere:
     Discovery (500 days): the best of 50 independent no-edge candidates by Sharpe (stand-in for the optimisation
       + P4-P7 selection; simplification: the real grid candidates are correlated and P7 has other gates).
     V0 (500) + V1 (115): kept only if the candidate PASSES P8a as implemented:
       evidence p (production robustlab.stats.validation.evidence, 2,000 bootstrap reps, per-period segments),
       Holm at 5% in a family of K = 5 (the other four p-values drawn U(0,1)), pooled validation profit > 0, and
       profit after the 1x cost-stress width per closing deal > 0 (COST_FRAGILE). Reproducibility / ruin gates
       always pass in this model.
   Processes: R4-iid, R4-GARCH(1,1) 0.05/0.90, R4-AR(1) 0.2. Future truth: the same process, no edge.
   k_safe with the draft-2 scenario set. 200 repetitions.
   Acceptance: true P(DD > 20%) <= 5% in >= 90% of repetitions AND true P(ruin) <= 1% in >= 95%.
   Recorded: the same with S0 only, k_safe / k_oracle, the binding constraint, the mean validation Sharpe of the
   accepted histories and the P8a acceptance rate.

 R5 (ADDED AFTER seeing R4: R4 ar1 was not met). Information for the decision only, NOT an acceptance test:
   the same R4 / R3b runs with one more DD-limited scenario S63 = S6 + S3 (no edge AND AR(1) rho 0.3 on the
   demeaned P&L; both were already approved stresses, but only their combination with everything else (SC) was
   limited, and SC carries no DD limit). Enabled with P9_S63=1; the output lines are tagged "R5".
Usage: uv run python docs/research/p9_simulations/risk_sim2.py r3a:<D> | r3b:<D> | r4:<iid|garch|ar1>
"""
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import risk_sim as rs  # noqa: E402

from robustlab.stats import validation as ev  # noqa: E402

REPS = int(os.environ.get("P9_REPS", "200"))
REFERENCE = ("S1a", "S1a3")
S63 = os.environ.get("P9_S63") == "1"
TAG = "R5 " if S63 else ""


def trades_from_days(x, seg_lens, hold):
    out, pos = [], 0
    for n in seg_lens:
        s = x[pos:pos + n]
        m = n // hold
        if m:
            out.append(s[:m * hold].reshape(m, hold).sum(axis=1))
        pos += n
    return np.concatenate(out)


def scenarios2(h, rng, n_paths=rs.N_PATHS, only_s0=False):
    sc, b = rs.scenarios(h, rng, n_paths, only_s0)
    if only_s0:
        return sc, b
    x, d, pos = h["x"], h["d"], h["pos"]
    del sc["S1"]
    j = rng.integers(0, len(x), (n_paths, rs.H))
    sc["S1b"] = (x[j], d[j], pos[j])
    sc["S1a"] = (x[j], np.zeros((n_paths, rs.H)), np.zeros((n_paths, rs.H)))
    t3 = trades_from_days(x, h["seg_lens"], 3)
    j3 = rng.integers(0, len(t3), (n_paths, rs.H // 3))
    sc["S1a3"] = (t3[j3], np.zeros((n_paths, rs.H // 3)), np.zeros((n_paths, rs.H // 3)))
    if S63:
        X0, D0, P0 = sc["S6"]  # S0 demeaned
        e = np.empty_like(X0)
        e[:, 0] = X0[:, 0]
        for t in range(1, rs.H):
            e[:, t] = 0.3 * e[:, t - 1] + math.sqrt(1 - 0.09) * X0[:, t]
        sc["S63"] = (e, D0, P0)
    return sc, b


def k_safe2(sc):
    lim = {}
    for name, (X, D, P) in sc.items():
        if name in REFERENCE:
            continue
        if name != "SC":
            lim[(name, "dd")] = rs.allowed(rs.crit_dd(X, D), rs.ALPHA)
        lim[(name, "ruin")] = rs.allowed(rs.crit_ruin(X, D), rs.P_RUIN)
        lim[(name, "stop_out")] = rs.allowed(rs.crit_so(X, D, P), rs.P_SO)
    key = min(lim, key=lim.get)
    return lim[key], key


def coverage(tag, dgp_future, hist_fn, rng, extra=None):
    rows, keys = [], {}
    for _ in range(REPS):
        h = hist_fn(rng)
        sc, _ = scenarios2(h, rng)
        ks, key = k_safe2(sc)
        s0, _ = scenarios2(h, rng, only_s0=True)
        k0 = rs.k_safe(s0)[0]
        Xf, Df, _ = dgp_future(rng)
        tdd, trn = rs.crit_dd(Xf, Df), rs.crit_ruin(Xf, Df)
        k_or = float(np.quantile(tdd, rs.ALPHA))
        rows.append(((tdd <= ks).mean(), (trn <= ks).mean(), (tdd <= k0).mean(), ks / k_or, k0 / k_or))
        keys[f"{key[0]}:{key[1]}"] = keys.get(f"{key[0]}:{key[1]}", 0) + 1
    a = np.array(rows, dtype=float)
    ok_dd, ok_r = float((a[:, 0] <= rs.ALPHA).mean()), float((a[:, 1] <= rs.P_RUIN).mean())
    met = ok_dd >= 0.90 and ok_r >= 0.95
    print(f"{tag}: share(true P(DD>20%) <= 5%) = {ok_dd:.3f}, median {np.median(a[:, 0]):.4f}, 90th pct "
          f"{np.quantile(a[:, 0], 0.9):.4f}; share(ruin ok) = {ok_r:.3f}; k_safe/k_oracle median "
          f"{np.median(a[:, 3]):.3f} [10%-90%: {np.quantile(a[:, 3], 0.1):.3f}-{np.quantile(a[:, 3], 0.9):.3f}] -> "
          f"{'OK' if met else 'NOT MET'}")
    print(f"{tag}: S0 only: share(DD ok) = {float((a[:, 2] <= rs.ALPHA).mean()):.3f}, median "
          f"{np.median(a[:, 2]):.4f}; k_S0/k_oracle median {np.median(a[:, 4]):.3f}")
    print(f"{tag}: binding {dict(sorted(keys.items(), key=lambda kv: -kv[1]))}")
    if extra:
        print(f"{tag}: {extra()}")


def r3a(dgp):
    rng = np.random.default_rng([rs.SEED, 30, int(dgp[1:])])
    r = {k: [] for k in ("S0", "S1b", "S1a", "S1a3")}
    for _ in range(REPS):
        h = rs.history(dgp, rng)
        sc, _ = scenarios2(h, rng)
        for k in r:
            X, D, _ = sc[k]
            r[k].append(rs.allowed(rs.crit_dd(X, D), rs.ALPHA))
    a = {k: np.array(v) for k, v in r.items()}
    q = lambda v: f"median {np.median(v):.3f} [10%-90%: {np.quantile(v, 0.1):.3f}-{np.quantile(v, 0.9):.3f}]"
    print(f"R3a {dgp}: DD-allowed lot S0 {np.median(a['S0']):.2f}, S1b {np.median(a['S1b']):.2f}, "
          f"S1a {np.median(a['S1a']):.2f}, S1a 3-day trades {np.median(a['S1a3']):.2f}")
    print(f"R3a {dgp}: k_S1a/k_S1b {q(a['S1a'] / a['S1b'])}; k_S1a3/k_S1b {q(a['S1a3'] / a['S1b'])}; "
          f"k_S1b/k_S0 {q(a['S1b'] / a['S0'])}")


def r3b(dgp):
    rng = np.random.default_rng([rs.SEED, 31, int(dgp[1:])])
    coverage(f"{TAG}R3b {dgp}", lambda g: rs.gen_future_fast(dgp, rs.N_TRUE, g), lambda g: rs.history(dgp, g), rng)


# ---------- R4 ----------

def _z(kind, shape, rng):
    """Unit-variance noise, rows independent."""
    n_rows, n = shape
    if kind == "iid":
        return rng.standard_normal(shape)
    if kind == "garch":
        a, bb = 0.05, 0.90
        z = np.empty(shape)
        h, e = np.ones(n_rows), np.zeros(n_rows)
        for i in range(n):
            h = (1 - a - bb) + a * e * e + bb * h
            e = np.sqrt(h) * rng.standard_normal(n_rows)
            z[:, i] = e
        return z
    r = 0.2
    z = np.empty(shape)
    z[:, 0] = rng.standard_normal(n_rows)
    for i in range(1, n):
        z[:, i] = r * z[:, i - 1] + math.sqrt(1 - r * r) * rng.standard_normal(n_rows)
    return z


def _extras(n, vol, rng):
    return (-np.abs(rng.normal(0, 0.5 * vol, n)), rng.poisson(0.5, n).astype(float),
            (rng.random(n) < 0.7).astype(float))


STATS = {"tries": 0, "accepted": 0, "val_sharpe": []}


def r4_history(kind, rng):
    n_d, n_v0, n_v1 = rs.SEGS
    disc = rs.SIGMA * _z(kind, (50, n_d), rng)
    sh = disc.mean(axis=1) / disc.std(axis=1, ddof=1)
    xd = disc[int(np.argmax(sh))]
    while True:
        STATS["tries"] += 1
        v = rs.SIGMA * _z(kind, (1, n_v0 + n_v1), rng)[0]
        v0, v1 = v[:n_v0], v[n_v0:]
        sd = v.std(ddof=1)
        sr = v.mean() / sd
        from scipy.stats import kurtosis, norm, skew
        g3, g4 = skew(v), kurtosis(v, fisher=False)
        var = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
        if var <= 0 or 1 - norm.cdf(sr * math.sqrt(len(v) - 1) / math.sqrt(var)) > 0.05 / 5:
            continue  # p >= 1 - PSR_standard > alpha / K: Holm can never pass it
        dv, cv, pv = _extras(len(v), rs.SIGMA, rng)
        net = v.sum()
        if net <= 0 or net - (cv * rs.WIDTH).sum() <= 0:
            continue
        e = ev.evidence([v0, v1], 2000, rng)
        fam = {"target": e["p"], **{f"o{i}": float(rng.random()) for i in range(4)}}
        if not ev.holm(fam)["target"]:
            continue
        STATS["accepted"] += 1
        STATS["val_sharpe"].append(sr)
        dd, cd, pd = _extras(n_d, rs.SIGMA, rng)
        return {"x": np.concatenate([xd, v]), "d": np.concatenate([dd, dv]), "c": np.concatenate([cd, cv]),
                "pos": np.concatenate([pd, pv]), "seg_lens": list(rs.SEGS)}


def r4(kind):
    rng = np.random.default_rng([rs.SEED, 40, ("iid", "garch", "ar1").index(kind)])

    def future(g):
        x = rs.SIGMA * _z(kind, (rs.N_TRUE, rs.H), g)
        d = -np.abs(g.normal(0, 0.5 * rs.SIGMA, (rs.N_TRUE, rs.H)))
        return x, d, None

    coverage(f"{TAG}R4 {kind}", future, lambda g: r4_history(kind, g), rng,
             lambda: f"P8a acceptance {STATS['accepted']}/{STATS['tries']} = "
                     f"{STATS['accepted'] / STATS['tries']:.4f}; mean validation daily Sharpe of accepted histories "
                     f"{np.mean(STATS['val_sharpe']):.4f}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        k, v = arg.split(":")
        {"r3a": r3a, "r3b": r3b, "r4": r4}[k](v)
