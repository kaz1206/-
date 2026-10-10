"""P6 review round 2c: the most conservative of standard / AR(1) n* / Newey-West n* / bootstrap SE.
Seed 20261011. Usage: uv run --with arch==8.0.0 python dsr_mc6.py <reps> <boot_reps>"""
import sys

import numpy as np
from scipy.stats import kurtosis, norm, skew

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from dsr_mc4 import N, T, cp, gen, n_ar1, n_bartlett, psr, sr0  # noqa: E402
from dsr_mc5 import se_boot  # noqa: E402


def run(name, g, reps, breps):
    rng = np.random.default_rng(20261011)
    k_all = 0
    for _ in range(reps):
        x = g(rng)
        s = x.mean(0) / x.std(0, ddof=1)
        k = int(np.argmax(s))
        xk = x[:, k]
        g3, g4 = skew(xk), kurtosis(xk, fisher=False)
        s0 = sr0(N, s.var(ddof=1))
        se, _ = se_boot(rng, xk, breps)
        vals = [psr(s[k], T, g3, g4, s0), psr(s[k], n_ar1(T, xk), g3, g4, s0),
                psr(s[k], n_bartlett(T, xk), g3, g4, s0), norm.cdf((s[k] - s0) / se)]
        k_all += min(vals) >= .95
    print(f"{name:28s} minAll4={k_all / reps:.4f}{cp(k_all, reps)}")


if __name__ == "__main__":
    R, B = int(sys.argv[1]), int(sys.argv[2])
    for name, kind, rho, mu in [("AR1 0.4 rho0.9", "ar1_0.4", 0.9, 0), ("AR2 .3/.3 rho0.9", "ar2", 0.9, 0),
                                ("MA5 rho0.9", "ma5", 0.9, 0), ("GARCH+AR1 0.4 rho0.9", "garch_ar1", 0.9, 0),
                                ("regime vol+AR1 0.4 rho0.9", "regime_ar1", 0.9, 0),
                                ("POWER iid rho0.3 mu.12", "iid", 0.3, 0.12),
                                ("POWER AR1 0.2 rho0.3 mu.12", "ar1_0.2", 0.3, 0.12),
                                ("POWER AR1 0.4 rho0.3 mu.20", "ar1_0.4", 0.3, 0.20)]:
        run(name, gen(kind, rho, mu), R, B)
