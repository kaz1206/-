"""P6 review round 2d. (1) N/V scope when the best of two rounds is selected, each round with its own common
factor (within-round correlation 0.9). (2) Stationary-bootstrap CI coverage under several dependence forms.
Seed 20261011. Usage: uv run --with arch==8.0.0 python scope_cov.py <scope_reps> <cov_reps> <boot_reps>"""
import sys

import numpy as np
from arch.bootstrap import IIDBootstrap, StationaryBootstrap, optimal_block_length
from scipy.signal import lfilter
from scipy.stats import kurtosis, skew

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from dsr_mc4 import N, T, cp, psr, sr0  # noqa: E402


def scope(reps, rho=0.9, rounds=2):
    rng = np.random.default_rng(20261011)
    c = np.zeros(2, int)
    for _ in range(reps):
        xs = []
        for _r in range(rounds):
            f = rng.standard_normal((T, 1))
            xs.append(np.sqrt(rho) * f + np.sqrt(1 - rho) * rng.standard_normal((T, N)))
        x = np.hstack(xs)
        s = x.mean(0) / x.std(0, ddof=1)
        k = int(np.argmax(s))
        r = k // N
        g3, g4 = skew(x[:, k]), kurtosis(x[:, k], fisher=False)
        c[0] += psr(s[k], T, g3, g4, sr0(N, s[r * N:(r + 1) * N].var(ddof=1))) >= .95   # round N and V
        c[1] += psr(s[k], T, g3, g4, sr0(rounds * N, s.var(ddof=1))) >= .95             # family N and V
    print(f"scope rho{rho} rounds{rounds}: round={c[0] / reps:.4f}{cp(c[0], reps)} family={c[1] / reps:.4f}{cp(c[1], reps)}")


def series(kind, rng, n=520):
    z = rng.standard_normal(n)
    if kind == "ar1_0.5":
        return lfilter([1], [1, -0.5], z)
    if kind == "ar2":
        return lfilter([1], [1, -0.3, -0.3], z)
    if kind == "ma5":
        return lfilter(np.ones(6) / np.sqrt(6), [1], z)
    if kind == "garch_ar1":
        h, u = 1.0, np.empty(n)
        for t in range(n):
            u[t] = np.sqrt(h) * z[t]
            h = 0.05 + 0.1 * u[t] ** 2 + 0.85 * h
        return lfilter([1], [1, -0.4], u)
    raise ValueError(kind)


def coverage(kind, reps, breps):
    rng = np.random.default_rng(20261011)
    c = np.zeros(2, int)
    for _ in range(reps):
        x = series(kind, rng)
        b = max(1.0, float(optimal_block_length(x)["stationary"].iloc[0]))
        for i, bs in enumerate((StationaryBootstrap(b, x, seed=rng), IIDBootstrap(x, seed=rng))):
            ci = bs.conf_int(np.mean, reps=breps, method="percentile", size=0.90)
            c[i] += ci[0, 0] <= 0 <= ci[1, 0]
    print(f"coverage {kind:10s} SB={c[0] / reps:.4f}{cp(c[0], reps)} IID={c[1] / reps:.4f}{cp(c[1], reps)}")


if __name__ == "__main__":
    S, C, B = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    scope(S, 0.9, 2)
    scope(S, 0.9, 5)
    for kind in ("ar1_0.5", "ar2", "ma5", "garch_ar1"):
        coverage(kind, C, B)
