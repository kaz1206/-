"""P8b plan: the pre-registered OOS prediction band, before implementation. Seed 20261011.

Validation = V0 (516 days) + V1 (115 days), generated separately and aggregated as P8a (§4.2.1).
OOS = one period of 520 days. The band is fixed BEFORE the OOS run from validation data only:
  V = 1 - g3*SR + (g4 - 1)/4*SR^2 (validation), n_eff = min(n, n_AR1, n_NW) (validation, per-period ACF)
  se_val = max(sqrt(V / (n_eff - 1)), bootstrap SE)          (validation)
  se_oos = sqrt(V / (n_oos * n_eff / n - 1))                  (predicted from validation, n_oos known in advance)
  lower  = SR_val - 1.645 * sqrt(se_val^2 + se_oos^2)         (one-sided 95%)
OOS fails when SR_oos < lower (OOS_BELOW_PREDICTION_BAND) or the OOS mean <= 0 (OOS_LOSS).
Cases: s1 = edge persists (false rejection of a real edge), s2 = edge decays to 0 in the OOS (detection),
s3 = no edge at all, K = 5 candidates through the P8a gate (Holm) and then the OOS gates (chain pass rate).
Usage: uv run python docs/research/p8_simulations/oos_band.py s1:<kind>:<mu> s2:<kind>:<mu> s3:<kind>
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import validation_gate2 as vg  # noqa: E402

from robustlab.stats import validation as ev  # noqa: E402

N_OOS, SEED, REPS = 520, 20261011, 2000


def band_lower(segs, rng):
    e = ev.evidence(segs, vg.B, rng)
    sr, g3, g4, n = e["sharpe"], e["skewness"], e["kurtosis"], e["n"]
    v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
    n_eff = min(n, e["n_ar1"], e["n_newey_west"])
    se_v = max(math.sqrt(v / (n_eff - 1)), e["bootstrap_se"] or 0.0)
    se_o = math.sqrt(v / (N_OOS * n_eff / n - 1))
    return sr - 1.645 * math.sqrt(se_v ** 2 + se_o ** 2), e["p"]


def oos(kind, rng, k, rho, mus):
    x = vg.period(kind, rng, N_OOS, k, rho)
    mus = np.asarray(mus, dtype=float)
    return x + mus * x.std(axis=0, ddof=1) if mus.any() else x


def fails(x, lower):
    sr = x.mean() / x.std(ddof=1)
    return sr < lower, x.mean() <= 0


def s1(kind, mu, decay=False):
    mu = float(mu)
    rng = np.random.default_rng(SEED)
    band = loss = either = 0
    for _ in range(REPS):
        x0, x1 = vg.gen(kind, rng, 1, 0.0, [mu])
        lower, _ = band_lower([x0[:, 0], x1[:, 0]], rng)
        y = oos(kind, rng, 1, 0.0, [0.0 if decay else mu])[:, 0]
        b, lo = fails(y, lower)
        band += b
        loss += lo
        either += b or lo
    tag = "s2 decays to 0" if decay else "s1 persists"
    print(f"{tag} {kind:10s} mu {mu}: below band {band / REPS:.4f}{vg.cp(band, REPS)}  OOS loss {loss / REPS:.4f}"
          f"  fail either {either / REPS:.4f}{vg.cp(either, REPS)}", flush=True)


def s2(kind, mu):
    s1(kind, mu, decay=True)


def s3(kind):
    rng = np.random.default_rng(SEED)
    p8a = chain = 0
    for _ in range(REPS):
        x0, x1 = vg.gen(kind, rng, 5, 0.5, [0.0] * 5)
        res = [band_lower([x0[:, j], x1[:, j]], rng) for j in range(5)]
        rej = ev.holm({f"{j:02d}": p for j, (_, p) in enumerate(res)})
        passed = [j for j in range(5) if rej[f"{j:02d}"]]
        p8a += bool(passed)
        if passed:
            y = oos(kind, rng, 5, 0.5, [0.0] * 5)
            chain += any(not any(fails(y[:, j], res[j][0])) for j in passed)
    print(f"s3 no edge K=5 {kind:10s}: some candidate passes P8a {p8a / REPS:.4f}{vg.cp(p8a, REPS)}  "
          f"passes P8a and OOS {chain / REPS:.4f}{vg.cp(chain, REPS)}", flush=True)


if __name__ == "__main__":
    for case in sys.argv[1:]:
        name, *args = case.split(":")
        {"s1": s1, "s2": s2, "s3": s3}[name](*args)
