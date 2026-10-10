"""P6 review round 2b: PSR/DSR with the Sharpe standard error estimated by the stationary bootstrap
(Politis-Romano; mean block length by Politis-White via arch.bootstrap.optimal_block_length).
z = (SR - SR0) / SE_boot, judged with min(standard DSR, bootstrap DSR). Seed 20261011.
Usage: uv run --with arch==8.0.0 python dsr_mc5.py <reps> <boot_reps>"""
import sys

import numpy as np
from arch.bootstrap import optimal_block_length
from scipy.stats import kurtosis, norm, skew

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from dsr_mc4 import N, T, cp, gen, psr, sr0  # noqa: E402


def sb_indices(rng, n, b, reps):
    p = 1.0 / b
    idx = np.empty((reps, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, reps)
    new = rng.random((reps, n)) < p
    starts = rng.integers(0, n, (reps, n))
    for t in range(1, n):
        idx[:, t] = np.where(new[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def se_boot(rng, x, reps):
    b = max(1.0, float(optimal_block_length(x)["stationary"].iloc[0]))
    xs = x[sb_indices(rng, len(x), b, reps)]
    s = xs.mean(1) / xs.std(1, ddof=1)
    return float(s.std(ddof=1)), b


def run(name, g, reps, breps):
    rng = np.random.default_rng(20261011)
    c = np.zeros(2, int)
    for _ in range(reps):
        x = g(rng)
        s = x.mean(0) / x.std(0, ddof=1)
        k = int(np.argmax(s))
        xk = x[:, k]
        s0 = sr0(N, s.var(ddof=1))
        std = psr(s[k], T, skew(xk), kurtosis(xk, fisher=False), s0)
        se, _ = se_boot(rng, xk, breps)
        boot = norm.cdf((s[k] - s0) / se)
        c += [std >= .95, min(std, boot) >= .95]
    print(f"{name:28s} DSR={c[0] / reps:.4f}{cp(c[0], reps)}  minBoot={c[1] / reps:.4f}{cp(c[1], reps)}")


if __name__ == "__main__":
    R, B = int(sys.argv[1]), int(sys.argv[2])
    for name, kind, rho, mu in [("iid rho0", "iid", 0.0, 0), ("equicorr 0.9", "iid", 0.9, 0),
                                ("AR1 0.4 rho0.9", "ar1_0.4", 0.9, 0), ("AR2 .3/.3 rho0.9", "ar2", 0.9, 0),
                                ("MA5 rho0.9", "ma5", 0.9, 0), ("GARCH rho0.9", "garch", 0.9, 0),
                                ("GARCH+AR1 0.4 rho0.9", "garch_ar1", 0.9, 0),
                                ("regime vol+AR1 0.4 rho0.9", "regime_ar1", 0.9, 0),
                                ("POWER iid rho0.3 mu.12", "iid", 0.3, 0.12),
                                ("POWER AR1 0.2 rho0.3 mu.12", "ar1_0.2", 0.3, 0.12),
                                ("POWER AR1 0.4 rho0.3 mu.12", "ar1_0.4", 0.3, 0.12),
                                ("POWER AR1 0.4 rho0.3 mu.20", "ar1_0.4", 0.3, 0.20)]:
        run(name, gen(kind, rho, mu), R, B)
