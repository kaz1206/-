"""P8b plan, independent check of the prediction-band formula (requested 2026-10-11). Seed 20261011.

Checks (acceptance written here BEFORE running):
 C1 hand case: a 7-day two-period example computed step by step with plain Python (math only); the
    production-based values (robustlab.stats.validation.evidence) must agree to 1e-12.
 C2 independent reference: the same quantities re-coded with lists and loops (no numpy, no robustlab); 300 random
    two-period data sets; agreement to 1e-9.
 C3 empirical calibration (the truth): true daily Sharpe 0.05, 4,000 repetitions per process. The empirical SD of
    SR_val, SR_oos and SR_oos - SR_val is compared with the mean predicted SE. Acceptance for independent, AR(1) 0.4,
    V1 with doubled volatility and GARCH + AR(1) 0.4: predicted / empirical for SR_oos - SR_val in [0.95, 1.30]
    (not too narrow, at most 30% too wide). AR(2) and MA(5) are recorded.
 C4 conditional on passing P8a (the real use: only P8a passes reach P8b): P(SR_oos below the band | P8a pass) with a
    persistent edge; recorded (2,000 repetitions).
Two formulas are evaluated side by side:
  orig: se_oos = sqrt(V / (n_oos * n_eff / n - 1)), n_eff = min(n, n_AR1, n_NW)      (plan draft 1)
  fix1: n_eff* = V / se_val^2 + 1 with se_val = max(analytic, bootstrap); se_oos = sqrt(V / (n_oos * n_eff* / n - 1))
        (so that se_oos carries the same dependence correction as se_val, including the bootstrap one)
Usage: uv run python docs/research/p8_simulations/oos_band_check.py c1 c2 c3:<kind> c4:<kind>:<mu>
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import validation_gate2 as vg  # noqa: E402

from robustlab.stats import validation as ev  # noqa: E402

N_OOS, SEED, Z = 520, 20261011, 1.6448536269514722


def band(e, n_oos=N_OOS):
    """Both formulas from the evidence dict of the validation periods."""
    sr, g3, g4, n = e["sharpe"], e["skewness"], e["kurtosis"], e["n"]
    v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    n_eff = min(n, e["n_ar1"], e["n_newey_west"])
    se_an = math.sqrt(v / (n_eff - 1))
    se_v = max(se_an, e["bootstrap_se"] or 0.0)
    se_o = math.sqrt(v / (n_oos * n_eff / n - 1))
    n_star = v / se_v ** 2 + 1
    se_o1 = math.sqrt(v / (n_oos * n_star / n - 1))
    return {"sr": sr, "v": v, "n_eff": n_eff, "se_an": se_an, "se_v": se_v, "se_o": se_o, "se_o1": se_o1,
            "lower": sr - Z * math.sqrt(se_v ** 2 + se_o ** 2), "lower1": sr - Z * math.sqrt(se_v ** 2 + se_o1 ** 2)}


# ----- C2: independent reference (lists and loops only) -----

def ref(segs, n_oos=N_OOS):
    xs = [float(v) for s in segs for v in s]
    n = len(xs)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    sr = m / sd
    m2 = sum((x - m) ** 2 for x in xs) / n
    g3 = (sum((x - m) ** 3 for x in xs) / n) / m2 ** 1.5
    g4 = (sum((x - m) ** 4 for x in xs) / n) / m2 ** 2
    den = sum((x - m) ** 2 for x in xs)

    def rho(k):
        tot = 0.0
        for s in segs:
            s = [float(v) for v in s]
            for t in range(k, len(s)):
                tot += (s[t] - m) * (s[t - k] - m)
        return tot / den

    r1 = rho(1)
    n_ar1 = n * (1 - r1) / (1 + r1) if r1 > 0 else n
    lag = int(math.floor(4 * (n / 100) ** (2 / 9)))
    f = 1 + 2 * sum((1 - k / (lag + 1)) * rho(k) for k in range(1, lag + 1))
    n_nw = n / f if f > 1 else n
    n_eff = min(n, n_ar1, n_nw)
    v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    return {"sr": sr, "g3": g3, "g4": g4, "r1": r1, "lag": lag, "f": f, "n_eff": n_eff, "v": v,
            "se_an": math.sqrt(v / (n_eff - 1)), "se_o": math.sqrt(v / ((n_oos * n_eff) / n - 1))}


def c1():
    seg0, seg1 = [0.010, -0.005, 0.020, 0.000], [0.015, -0.010, 0.005]
    r = ref([seg0, seg1], n_oos=10)
    e = ev.evidence([np.array(seg0), np.array(seg1)], 50, np.random.default_rng(0))
    # evidence() returns p = 1 for n < 30, so compare the building blocks directly
    from scipy.stats import kurtosis, skew
    x = np.array(seg0 + seg1)
    prod = {"sr": x.mean() / x.std(ddof=1), "g3": float(skew(x)), "g4": float(kurtosis(x, fisher=False)),
            "r1": ev.acf_segments([np.array(seg0), np.array(seg1)], 1)}
    print("C1 hand case: seg0 =", seg0, "seg1 =", seg1, "n_oos = 10")
    for k in ("sr", "g3", "g4", "r1", "lag", "f", "n_eff", "v", "se_an", "se_o"):
        extra = f"  production {prod[k]:.15g}  diff {abs(prod[k] - r[k]):.2e}" if k in prod else ""
        print(f"   {k:6s} = {r[k]:.15g}{extra}")
    print("   (n = 7 < 30, so evidence() reports p = 1:", e["p"], ")", flush=True)


def c2(reps=300):
    rng = np.random.default_rng(SEED)
    worst = 0.0
    for _ in range(reps):
        n0, n1 = int(rng.integers(40, 300)), int(rng.integers(30, 120))
        s0 = rng.standard_t(5, n0) * 0.01 + 0.0005
        s1 = np.convolve(rng.standard_normal(n1 + 2), [1, 0.5, 0.2], "valid") * 0.02
        e = ev.evidence([s0, s1], 50, np.random.default_rng(1))
        b, r = band(e), ref([s0, s1])
        for k in ("sr", "v", "n_eff", "se_an", "se_o"):
            worst = max(worst, abs(b[k] - r[k]) / max(1.0, abs(r[k])))
    print(f"C2 independent reference: {reps} data sets, worst relative difference {worst:.2e} "
          f"({'OK' if worst < 1e-9 else 'MISMATCH'})", flush=True)


def c3(kind, reps=4000, mu=0.05):
    rng = np.random.default_rng(SEED)
    rows = []
    for _ in range(reps):
        x0, x1 = vg.gen(kind, rng, 1, 0.0, [mu])
        e = ev.evidence([x0[:, 0], x1[:, 0]], vg.B, rng)
        y = vg.period(kind, rng, N_OOS, 1, 0.0)[:, 0]
        y = y + mu * y.std(ddof=1)
        b = band(e)
        rows.append((b["sr"], y.mean() / y.std(ddof=1), b["se_an"], b["se_v"], b["se_o"], b["se_o1"]))
    a = np.array(rows)
    sd_v, sd_o, sd_d = a[:, 0].std(ddof=1), a[:, 1].std(ddof=1), (a[:, 1] - a[:, 0]).std(ddof=1)
    pd_orig = np.sqrt(a[:, 3] ** 2 + a[:, 4] ** 2).mean()
    pd_fix = np.sqrt(a[:, 3] ** 2 + a[:, 5] ** 2).mean()
    judged = kind in ("iid", "ar1_0.4", "vol2", "garch_ar1")

    def ok(r):
        return ("OK" if 0.95 <= r <= 1.30 else "OUT") if judged else "recorded"

    print(f"C3 {kind:10s}: SD(SR_val) emp {sd_v:.5f} pred analytic {a[:, 2].mean():.5f} pred max {a[:, 3].mean():.5f} | "
          f"SD(SR_oos) emp {sd_o:.5f} pred orig {a[:, 4].mean():.5f} fix1 {a[:, 5].mean():.5f} | "
          f"SD(diff) emp {sd_d:.5f} pred orig {pd_orig:.5f} ({pd_orig / sd_d:.3f} {ok(pd_orig / sd_d)}) "
          f"fix1 {pd_fix:.5f} ({pd_fix / sd_d:.3f} {ok(pd_fix / sd_d)})", flush=True)


def c4(kind, mu, reps=2000):
    mu = float(mu)
    rng = np.random.default_rng(SEED)
    passed = below = below1 = 0
    for _ in range(reps):
        x0, x1 = vg.gen(kind, rng, 1, 0.0, [mu])
        e = ev.evidence([x0[:, 0], x1[:, 0]], vg.B, rng)
        y = vg.period(kind, rng, N_OOS, 1, 0.0)[:, 0]
        y = y + mu * y.std(ddof=1)
        if e["p"] > 0.05:
            continue
        passed += 1
        b = band(e)
        sr_o = y.mean() / y.std(ddof=1)
        below += sr_o < b["lower"]
        below1 += sr_o < b["lower1"]
    print(f"C4 {kind:10s} mu {mu}: P8a pass {passed / reps:.4f}; below band | pass: orig {below / passed:.4f}"
          f"{vg.cp(below, passed)} fix1 {below1 / passed:.4f}{vg.cp(below1, passed)}", flush=True)


if __name__ == "__main__":
    for case in sys.argv[1:]:
        name, *args = case.split(":")
        {"c1": c1, "c2": c2, "c3": c3, "c4": c4}[name](*args)
