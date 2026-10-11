"""P9 post-implementation re-verification (P9_PLAN §7.1): R1-R4 with the PRODUCTION engine
(robustlab.risk.engine), same conditions and seeds as the pre-implementation simulation
(docs/research/p9_simulations/risk_sim.py, risk_sim2.py). Acceptance unchanged:
  R1a critical lots == brute force (identical counts); R1b / R1c ruin vs closed form (4 SE + 0.003 / 4 SE + 0.0005);
  R2 (= R3b with the final scenario set: S1b limits, S1a reference, S63 added by decision Q7) for D1..D6 and
  R4 (P8a-faithful selection, no edge) iid / garch / ar1: true P(DD > 20%) <= 5% in >= 90% of repetitions AND
  true P(ruin) <= 1% in >= 95%. D7 and R3a are recorded only.
Histories and true futures come from the prototype generators with the same seeds; the scenario paths and every
limit come from the production engine (its draw order differs from the prototype, so the numbers are not
bit-identical; the R1 inputs are, and R1 must reproduce the pre-implementation numbers exactly).
Usage: uv run python docs/research/p9_validation/run_checks.py r1 | r2:<D1..D7> | r4:<iid|garch|ar1> | r3a:<D>
"""
import math
import os
import sys
from pathlib import Path

import numpy as np
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1] / "p9_simulations"
sys.path.insert(0, str(ROOT))
import risk_sim as rs  # noqa: E402
import risk_sim2 as r2  # noqa: E402

from robustlab.risk import engine as eng  # noqa: E402

REPS = int(os.environ.get("P9_REPS", "200"))
SO_PCT = rs.SO * 100.0


def r1():
    rng = np.random.default_rng(rs.SEED)
    bad = 0
    for case in range(60):
        n = 300
        X = rng.normal(rng.uniform(-0.1, 0.2) * rs.SIGMA, rs.SIGMA, (n, rs.H))
        D = -np.abs(rng.normal(0, 0.5 * rs.SIGMA, (n, rs.H)))
        P = (rng.random((n, rs.H)) < 0.7).astype(float)
        cd, cr = eng.crit_dd(X, D, rs.L), eng.crit_ruin(X, D, rs.RUIN)
        cs = eng.crit_stop_out(X, D, P, so_percent=SO_PCT, margin=rs.MARGIN)
        for k in np.linspace(0.2, 40, 40):
            if (int((cd <= k).sum()), int((cr <= k).sum()), int((cs <= k).sum())) != rs.brute(X, D, P, k):
                bad += 1
    print(f"R1a (production): mismatches {bad} of 2400 -> {'OK' if bad == 0 else 'NOT MET'}")
    for tag, settings, n, tol in (("R1b", ((0.0, 15), (0.0, 25), (0.05, 20), (0.05, 30), (0.10, 30), (-0.03, 15)),
                                   40000, 0.003),
                                  ("R1c", ((0.0, 2.0), (0.0, 2.5), (0.0, 3.0), (0.05, 3.0), (0.05, 4.0), (-0.03, 2.5)),
                                   200000, 0.0005)):
        ok = True
        for mu_sr, k in settings:
            X = rng.normal(mu_sr * rs.SIGMA, rs.SIGMA, (n, rs.H))
            est = float((eng.crit_ruin(X, np.zeros_like(X), rs.RUIN) <= k).mean())
            mu, s, a, T = k * mu_sr * rs.SIGMA, k * rs.SIGMA, rs.RUIN, rs.H
            a_bg = a + 0.5826 * s
            an = norm.cdf((-a_bg - mu * T) / (s * math.sqrt(T))) + \
                math.exp(-2 * mu * a_bg / s ** 2) * norm.cdf((-a_bg + mu * T) / (s * math.sqrt(T)))
            se = math.sqrt(max(an * (1 - an), 1e-12) / n)
            good = abs(est - an) <= 4 * se + tol
            ok &= good
            print(f"{tag} (production) sr={mu_sr:+.2f} k={k}: simulated {est:.5f}, closed form {an:.5f} -> "
                  f"{'OK' if good else 'NOT MET'}")
        print(f"{tag}:", "OK" if ok else "NOT MET")


def to_history(h):
    return eng.History(x=h["x"], d=h["d"], c=h["c"], pos=h["pos"], seg_lens=tuple(h["seg_lens"]), trades=h["x"])


def k_safe(h, rng, only_s0=False):
    lim = {}
    for name, X, D, P in eng.scenarios(to_history(h), rng, rs.N_PATHS, rs.H, rs.WIDTH):
        if name in eng.REFERENCE or (only_s0 and name != "S0"):
            continue
        if name in eng.LIMITED_DD:
            lim[(name, "dd")] = eng.allowed(eng.crit_dd(X, D, rs.L), rs.ALPHA)
        lim[(name, "ruin")] = eng.allowed(eng.crit_ruin(X, D, rs.RUIN), rs.P_RUIN)
        lim[(name, "stop_out")] = eng.allowed(eng.crit_stop_out(X, D, P, so_percent=SO_PCT, margin=rs.MARGIN),
                                              rs.P_SO)
        if only_s0:
            break
    key = min(lim, key=lim.get)
    return lim[key], key


def coverage(tag, future, hist, rng, extra=None, recorded=False):
    rows, keys = [], {}
    for _ in range(REPS):
        h = hist(rng)
        ks, key = k_safe(h, rng)
        k0, _ = k_safe(h, rng, only_s0=True)
        Xf, Df, _ = future(rng)
        tdd, trn = eng.crit_dd(Xf, Df, rs.L), eng.crit_ruin(Xf, Df, rs.RUIN)
        k_or = float(np.quantile(tdd, rs.ALPHA))
        rows.append(((tdd <= ks).mean(), (trn <= ks).mean(), (tdd <= k0).mean(), ks / k_or, k0 / k_or))
        keys[f"{key[0]}:{key[1]}"] = keys.get(f"{key[0]}:{key[1]}", 0) + 1
    a = np.array(rows, dtype=float)
    ok_dd, ok_r = float((a[:, 0] <= rs.ALPHA).mean()), float((a[:, 1] <= rs.P_RUIN).mean())
    met = ok_dd >= 0.90 and ok_r >= 0.95
    print(f"{tag}: share(true P(DD>20%) <= 5%) = {ok_dd:.3f}, median {np.median(a[:, 0]):.4f}, 90th pct "
          f"{np.quantile(a[:, 0], 0.9):.4f}; share(ruin ok) = {ok_r:.3f}; k_safe/k_oracle median "
          f"{np.median(a[:, 3]):.3f} [10%-90%: {np.quantile(a[:, 3], 0.1):.3f}-{np.quantile(a[:, 3], 0.9):.3f}] -> "
          f"{'OK' if met else 'NOT MET'}{' (recorded only)' if recorded else ''}")
    print(f"{tag}: S0 only: share(DD ok) = {float((a[:, 2] <= rs.ALPHA).mean()):.3f}; k_S0/k_oracle median "
          f"{np.median(a[:, 4]):.3f}")
    print(f"{tag}: binding {dict(sorted(keys.items(), key=lambda kv: -kv[1]))}")
    if extra:
        print(f"{tag}: {extra()}")


def r2_(dgp):
    rng = np.random.default_rng([rs.SEED, int(dgp[1:])])
    coverage(f"R2 {dgp} (production)", lambda g: rs.gen_future_fast(dgp, rs.N_TRUE, g), lambda g: rs.history(dgp, g),
             rng, recorded=dgp == "D7")


def r4(kind):
    rng = np.random.default_rng([rs.SEED, 40, ("iid", "garch", "ar1").index(kind)])

    def future(g):
        x = rs.SIGMA * r2._z(kind, (rs.N_TRUE, rs.H), g)
        return x, -np.abs(g.normal(0, 0.5 * rs.SIGMA, (rs.N_TRUE, rs.H))), None

    coverage(f"R4 {kind} (production)", future, lambda g: r2.r4_history(kind, g), rng,
             lambda: f"P8a acceptance {r2.STATS['accepted']}/{r2.STATS['tries']}; mean validation daily Sharpe "
                     f"{np.mean(r2.STATS['val_sharpe']):.4f}")


def r3a(dgp):
    rng = np.random.default_rng([rs.SEED, 30, int(dgp[1:])])
    r = {k: [] for k in ("S0", "S1b", "S1a")}
    for _ in range(REPS):
        h = rs.history(dgp, rng)
        for name, X, D, _ in eng.scenarios(to_history(h), rng, rs.N_PATHS, rs.H, rs.WIDTH):
            if name in r:
                r[name].append(eng.allowed(eng.crit_dd(X, D, rs.L), rs.ALPHA))
    a = {k: np.array(v) for k, v in r.items()}
    print(f"R3a {dgp} (production): k_S1a/k_S1b median {np.median(a['S1a'] / a['S1b']):.3f}; k_S1b/k_S0 median "
          f"{np.median(a['S1b'] / a['S0']):.3f}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        k, _, v = arg.partition(":")
        {"r1": lambda _: r1(), "r2": r2_, "r4": r4, "r3a": r3a}[k](v)
