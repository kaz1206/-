"""P8 plan: size and power of a validation-period evidence gate, before implementation.

Gate per candidate: conservative PSR(0) on the pooled validation daily returns (min of standard / AR(1) n* /
Newey-West n* / stationary-bootstrap SE, i.e. robustlab.stats.dsr.deflated with N = 1), family of K selected
candidates controlled with Holm at alpha = 5% (p = 1 - PSR_conservative).
n = 630 validation days (about V0 2020-01..2021-12 + V1 2024-01..2024-06). Seed 20261011.
Usage: uv run python docs/research/p8_simulations/validation_gate.py <reps>
"""
import sys

import numpy as np
from scipy.signal import lfilter
from scipy.stats import beta, kurtosis, skew

from robustlab.stats import bootstrap, dsr

N_DAYS, ALPHA, SEED = 630, 0.05, 20261011


def cp(k, n):
    lo = beta.ppf(0.025, k, n - k + 1) if k > 0 else 0.0
    hi = beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
    return round(float(lo), 4), round(float(hi), 4)


def gen(kind, rng, k, rho, mu):
    z = np.sqrt(rho) * rng.standard_normal((N_DAYS, 1)) + np.sqrt(1 - rho) * rng.standard_normal((N_DAYS, k))
    if kind == "iid":
        x = z
    elif kind.startswith("ar1_"):
        x = lfilter([1], [1, -float(kind[4:])], z, axis=0)
    elif kind == "ar2":
        x = lfilter([1], [1, -0.3, -0.3], z, axis=0)
    elif kind == "ma5":
        x = lfilter(np.ones(6) / np.sqrt(6), [1], z, axis=0)
    elif kind == "garch_ar1":
        h = np.ones((N_DAYS, 1))
        u = np.empty_like(z)
        for t in range(N_DAYS):
            u[t] = np.sqrt(h[t]) * z[t]
            if t + 1 < N_DAYS:
                h[t + 1] = 0.05 + 0.1 * u[t].mean() ** 2 + 0.85 * h[t]
        x = lfilter([1], [1, -0.4], u, axis=0)
    else:
        raise ValueError(kind)
    sd = x.std(axis=0, ddof=1)
    return x + np.asarray(mu) * sd  # mu = true daily Sharpe per column


def psr_cons(x, rng):
    sr = float(x.mean() / x.std(ddof=1))
    se = bootstrap.stationary_sharpe_se(x, 200, rng)
    d = dsr.deflated(sr, len(x), float(skew(x)), float(kurtosis(x, fisher=False)), dsr.sr0(1, None), x, se)
    return d["dsr_conservative"] if d["dsr_conservative"] is not None else 0.0


def holm(ps, alpha=ALPHA):
    order = np.argsort(ps)
    rej = np.zeros(len(ps), bool)
    for i, j in enumerate(order):
        if ps[j] <= alpha / (len(ps) - i):
            rej[j] = True
        else:
            break
    return rej


def run(name, kind, k, rho, mus, reps):
    rng = np.random.default_rng(SEED)
    any_null = edge = 0
    for _ in range(reps):
        x = gen(kind, rng, k, rho, mus)
        ps = np.array([1 - psr_cons(x[:, j], rng) for j in range(k)])
        rej = holm(ps)
        null = np.array(mus) == 0
        any_null += bool(rej[null].any())
        edge += bool(rej[~null].any()) if (~null).any() else 0
    s = f"{name:42s} null passes (FWER)={any_null / reps:.4f}{cp(any_null, reps)}"
    if (np.array(mus) > 0).any():
        s += f"  edge passes (power)={edge / reps:.4f}{cp(edge, reps)}"
    print(s, flush=True)


if __name__ == "__main__":
    R = int(sys.argv[1])
    print("-- size: K selected candidates, all without edge --")
    for kind in ("iid", "ar1_0.4", "ar2", "ma5", "garch_ar1"):
        run(f"K=1 {kind}", kind, 1, 0.0, [0.0], R)
    for kind in ("iid", "ar2"):
        run(f"K=5 {kind} corr 0.5", kind, 5, 0.5, [0.0] * 5, R)
    print("-- power: one candidate with a true daily Sharpe, 4 without (K=5, corr 0.5) --")
    for kind in ("iid", "ar1_0.2"):
        for mu in (0.05, 0.08, 0.12):
            run(f"K=5 {kind} edge {mu}", kind, 5, 0.5, [mu, 0, 0, 0, 0], R)
    for mu in (0.05, 0.08, 0.12):
        run(f"K=1 iid edge {mu}", "iid", 1, 0.0, [mu], R)
