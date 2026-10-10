"""P6 review round 2: corrections under higher-order dependence / volatility change, power with autocorrelation,
and N/V scope (round vs family). Seed 20261011. Usage: uv run python dsr_mc4.py <reps>"""
import sys

import numpy as np
from scipy.signal import lfilter
from scipy.stats import beta, kurtosis, norm, skew

G = 0.5772156649015329
T, N = 500, 100


def psr(sr, n, g3, g4, s0=0.0):
    return norm.cdf((sr - s0) * np.sqrt(n - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr))


def sr0(n_trials, v):
    return np.sqrt(v) * ((1 - G) * norm.ppf(1 - 1 / n_trials) + G * norm.ppf(1 - 1 / (n_trials * np.e)))


def acf(x, k):
    x = x - x.mean()
    return float((x[k:] * x[:-k]).sum() / (x * x).sum())


def n_ar1(n, x):
    r = acf(x, 1)
    return n * (1 - r) / (1 + r) if r > 0 else n


def n_bartlett(n, x):
    lag = int(np.floor(4 * (n / 100) ** (2 / 9)))  # Newey-West (1994) rule of thumb
    f = 1 + 2 * sum((1 - k / (lag + 1)) * acf(x, k) for k in range(1, lag + 1))
    return n / f if f > 1 else n


def cp(k, r):  # Clopper-Pearson 95% two-sided
    lo = beta.ppf(0.025, k, r - k + 1) if k > 0 else 0.0
    hi = beta.ppf(0.975, k + 1, r - k) if k < r else 1.0
    return round(lo, 4), round(hi, 4)


def gen(kind, rho, mu=0.0):
    def g(rng):
        f = rng.standard_normal((T, 1))
        e = rng.standard_normal((T, N))
        z = np.sqrt(rho) * f + np.sqrt(1 - rho) * e
        if kind == "iid":
            x = z
        elif kind.startswith("ar1_"):
            x = lfilter([1], [1, -float(kind[4:])], z, axis=0)
        elif kind == "ar2":
            x = lfilter([1], [1, -0.3, -0.3], z, axis=0)
        elif kind == "ma5":
            x = lfilter(np.ones(6) / np.sqrt(6), [1], z, axis=0)
        elif kind == "garch":
            h = np.ones((T, 1))
            x = np.empty_like(z)
            for t in range(T):
                x[t] = np.sqrt(h[t]) * z[t]
                if t + 1 < T:
                    h[t + 1] = 0.05 + 0.1 * x[t].mean() ** 2 + 0.85 * h[t]
        elif kind == "garch_ar1":
            h = np.ones((T, 1))
            u = np.empty_like(z)
            for t in range(T):
                u[t] = np.sqrt(h[t]) * z[t]
                if t + 1 < T:
                    h[t + 1] = 0.05 + 0.1 * u[t].mean() ** 2 + 0.85 * h[t]
            x = lfilter([1], [1, -0.4], u, axis=0)
        elif kind == "regime_ar1":
            vol = np.where((np.arange(T) // 100) % 2 == 0, 0.5, 1.5)[:, None]
            x = lfilter([1], [1, -0.4], z * vol, axis=0)
        else:
            raise ValueError(kind)
        return x + mu
    return g


def trial(rng, g):
    x = g(rng)
    s = x.mean(0) / x.std(0, ddof=1)
    k = int(np.argmax(s))
    xk = x[:, k]
    g3, g4 = skew(xk), kurtosis(xk, fisher=False)
    s0 = sr0(N, s.var(ddof=1))
    std = psr(s[k], T, g3, g4, s0)
    a1 = psr(s[k], n_ar1(T, xk), g3, g4, s0)
    bw = psr(s[k], n_bartlett(T, xk), g3, g4, s0)
    return std >= .95, min(std, a1) >= .95, min(std, bw) >= .95, min(std, a1, bw) >= .95


def run(name, g, reps):
    rng = np.random.default_rng(20261011)
    c = np.zeros(4, int)
    for _ in range(reps):
        c += trial(rng, g)
    print(f"{name:28s}", "  ".join(f"{lbl}={k / reps:.4f}{cp(k, reps)}" for lbl, k in zip(
        ("DSR", "minAR1", "minNW", "minAll"), c)))


def scope(reps):
    """Two rounds of 100 independent null trials each; the best of BOTH rounds is selected."""
    rng = np.random.default_rng(20261011)
    c = np.zeros(2, int)
    for _ in range(reps):
        x = rng.standard_normal((T, 2 * N))
        s = x.mean(0) / x.std(0, ddof=1)
        k = int(np.argmax(s))
        rnd = slice(0, N) if k < N else slice(N, 2 * N)
        g3, g4 = skew(x[:, k]), kurtosis(x[:, k], fisher=False)
        c[0] += psr(s[k], T, g3, g4, sr0(N, s[rnd].var(ddof=1))) >= .95          # round scope
        c[1] += psr(s[k], T, g3, g4, sr0(2 * N, s.var(ddof=1))) >= .95           # family scope
    print("scope: round N/V =", round(c[0] / reps, 4), cp(c[0], reps), " family N/V =", round(c[1] / reps, 4),
          cp(c[1], reps))


if __name__ == "__main__":
    R = int(sys.argv[1])
    print("-- size (null) --")
    for name, kind, rho in [("iid rho0", "iid", 0.0), ("equicorr 0.9", "iid", 0.9), ("AR1 0.4 rho0.9", "ar1_0.4", 0.9),
                            ("AR2 .3/.3 rho0.9", "ar2", 0.9), ("MA5 rho0.9", "ma5", 0.9),
                            ("GARCH rho0.9", "garch", 0.9), ("GARCH+AR1 0.4 rho0.9", "garch_ar1", 0.9),
                            ("regime vol+AR1 0.4 rho0.9", "regime_ar1", 0.9)]:
        run(name, gen(kind, rho), R)
    print("-- power (edge) --")
    for name, kind, rho, mu in [("iid rho0.3 mu.12", "iid", 0.3, 0.12), ("AR1 0.2 rho0.3 mu.12", "ar1_0.2", 0.3, 0.12),
                                ("AR1 0.4 rho0.3 mu.12", "ar1_0.4", 0.3, 0.12),
                                ("AR1 0.4 rho0.3 mu.20", "ar1_0.4", 0.3, 0.20)]:
        run(name, gen(kind, rho, mu), R)
    scope(R)
