"""DSR formula, corrections, bootstrap intervals and edge cases (P6_PLAN §6.1 lower half, §6.2)."""

import math
from statistics import NormalDist

import numpy as np
import pytest
from arch.bootstrap import StationaryBootstrap
from scipy.stats import kurtosis, skew

from robustlab.stats import bootstrap, dsr
from robustlab.stats.psr import psr

ND = NormalDist()


def independent_sr0(n, v):
    """Written apart from the implementation (stdlib only). Same as quantstrat's maxZ * sqrt(V)."""
    g = 0.5772156649015329
    return math.sqrt(v) * ((1 - g) * ND.inv_cdf(1 - 1 / n) + g * ND.inv_cdf(1 - 1 / (n * math.e)))


def independent_psr(sr, n, g3, g4, s0):
    return ND.cdf((sr - s0) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr))


@pytest.mark.parametrize(("n", "v"), [(2, 0.001), (10, 0.0004), (1044, 0.0031), (10**6, 0.01)])
def test_sr0_matches_an_independent_formula(n, v):
    assert dsr.sr0(n, v).value == pytest.approx(independent_sr0(n, v), abs=1e-12)


def test_dsr_matches_an_independent_formula():
    x = np.random.default_rng(3).standard_t(5, 400) * 0.01 + 0.001
    sr, g3, g4 = x.mean() / x.std(ddof=1), skew(x), kurtosis(x, fisher=False)
    s0 = dsr.sr0(300, 0.002)
    d = dsr.deflated(sr, 400, g3, g4, s0, x)
    assert d["dsr"] == pytest.approx(independent_psr(sr, 400, g3, g4, s0.value), abs=1e-12)


def test_n_equal_one_is_psr_of_zero():
    s0 = dsr.sr0(1, 0.5)
    assert s0.value == 0.0 and "N = 1" in s0.reason
    d = dsr.deflated(0.1, 250, 0.0, 3.0, s0, None)
    assert d["dsr"] == pytest.approx(psr(0.1, 250, 0.0, 3.0, 0.0).value, abs=1e-15)


def test_zero_variance_across_trials_gives_sr0_zero_with_reason():
    s0 = dsr.sr0(50, 0.0)
    assert s0.value == 0.0 and "V[SR] = 0" in s0.reason
    assert dsr.sr0(50, None).value is None and dsr.sr0(50, None).reason


def test_corrections_on_a_known_ar1_series():
    x = np.zeros(500)
    e = np.random.default_rng(5).standard_normal(500)
    for t in range(1, 500):
        x[t] = 0.4 * x[t - 1] + e[t]
    dep = dsr.dependence(x)
    r1 = dep["acf"]["1"]
    assert r1 == pytest.approx(np.corrcoef(x[1:], x[:-1])[0, 1], abs=0.01)
    assert dep["n_ar1"] == pytest.approx(500 * (1 - r1) / (1 + r1))
    assert dep["nw_lag"] == math.floor(4 * (5 ** (2 / 9)))
    assert dep["serial_dependence_high"] is True
    sr, g3, g4 = x.mean() / x.std(ddof=1), skew(x), kurtosis(x, fisher=False)
    d = dsr.deflated(abs(sr) + 0.1, 500, g3, g4, dsr.sr0(10, 0.001), x, 0.08)
    # a smaller effective size moves the AR(1) DSR towards 0.5
    assert abs(d["dsr_ar1"] - 0.5) < abs(d["dsr"] - 0.5)
    assert d["dsr_conservative"] == min(d["dsr"], d["dsr_ar1"], d["dsr_newey_west"], d["dsr_bootstrap_se"])
    # negative autocorrelation never inflates n
    neg = dsr.dependence(np.tile([1.0, -1.0], 50) + np.random.default_rng(1).normal(0, 0.01, 100))
    assert neg["n_ar1"] == 100 and neg["serial_dependence_high"] is False


def test_conservative_without_bootstrap_uses_three_variants():
    x = np.random.default_rng(9).standard_normal(300) * 0.01 + 0.002
    d = dsr.deflated(x.mean() / x.std(ddof=1), 300, skew(x), kurtosis(x, fisher=False), dsr.sr0(20, 0.001), x)
    assert d["dsr_bootstrap_se"] is None and "not run" in d["dsr_bootstrap_se_reason"]
    assert d["dsr_conservative_components"] == ["dsr", "dsr_ar1", "dsr_newey_west"]


def test_missing_values_never_produce_a_number():
    d = dsr.deflated(None, 300, None, None, dsr.sr0(20, 0.001), None)
    assert d["dsr"] is None and d["dsr_reason"] and d["dsr_conservative"] is None
    d = dsr.deflated(0.1, 300, 0.0, 3.0, dsr.sr0(20, None), None)
    assert all(d[k] is None for k in ("dsr", "dsr_ar1", "dsr_newey_west", "dsr_bootstrap_se", "dsr_conservative"))
    assert "SR0 not computable" in d["dsr_reason"]


def test_max_drawdown_by_hand():
    # deposit 100; daily returns -0.1, +0.05, -0.2, +0.5: equity 90, 95, 75, 125
    dd, pct, broke = bootstrap.max_drawdown(np.array([-0.1, 0.05, -0.2, 0.5]), 100.0)
    assert dd == pytest.approx(25.0) and pct == pytest.approx(25.0) and broke is False  # peak 100 (the deposit)
    # rises first: equity 110, 120, 90 -> peak 120, DD 30 (25%)
    dd, pct, _ = bootstrap.max_drawdown(np.array([0.1, 0.1, -0.3]), 100.0)
    assert dd == pytest.approx(30.0) and pct == pytest.approx(25.0)
    # falls on day one: the deposit is the first peak
    dd, pct, _ = bootstrap.max_drawdown(np.array([-0.05, 0.01]), 100.0)
    assert dd == pytest.approx(5.0) and pct == pytest.approx(5.0)
    # goes through zero: kept as is, flagged
    dd, pct, broke = bootstrap.max_drawdown(np.array([-0.6, -0.6, 0.1]), 100.0)
    assert dd == pytest.approx(120.0) and broke is True


def series(seed=11, n=400):
    r = np.random.default_rng(seed)
    e = r.standard_normal(n) * 0.003
    x = np.empty(n)
    x[0] = e[0]
    for t in range(1, n):
        x[t] = 0.3 * x[t - 1] + e[t]
    return x + 0.0003


def test_bootstrap_is_reproducible_and_seed_dependent():
    x = series()
    a = bootstrap.evaluate(x, 10_000, 300, 42)
    b = bootstrap.evaluate(x, 10_000, 300, 42)
    c = bootstrap.evaluate(x, 10_000, 300, 43)
    assert a == b
    assert a["stationary"]["sharpe_ci"] != c["stationary"]["sharpe_ci"]
    assert a["status"] == "OK" and a["block_length"]["stationary"] >= 1


def test_percentile_interval_equals_arch_conf_int():
    x = series()
    bl = bootstrap.block_lengths(x)["stationary"]
    ours = bootstrap.evaluate(x, 10_000, 500, 77)["stationary"]["mean_ci"]
    rng = np.random.Generator(np.random.PCG64(77))
    ci = StationaryBootstrap(bl, x, seed=rng).conf_int(np.mean, reps=500, method="percentile", size=0.90)
    assert ours[0] == pytest.approx(ci[0, 0], abs=1e-15) and ours[1] == pytest.approx(ci[1, 0], abs=1e-15)


def test_point_values_and_units():
    x = series()
    r = bootstrap.evaluate(x, 10_000, 200, 1)
    assert r["point"]["sharpe"] == pytest.approx(x.mean() / x.std(ddof=1))
    assert r["point"]["mean_amount"] == pytest.approx(x.mean() * 10_000)
    lo, hi = r["stationary"]["mean_ci"]
    assert lo < x.mean() < hi
    assert r["stationary"]["mean_amount_ci"] == pytest.approx((lo * 10_000, hi * 10_000))
    dd = r["stationary"]["max_dd_distribution"]
    assert dd["p5"] <= dd["p50"] <= dd["p95"]
    assert r["circular_sensitivity"]["mean_ci"] is not None


@pytest.mark.parametrize(("x", "reason"), [
    (np.zeros(100), "zero variance"),
    (np.r_[np.full(50, 0.001), [np.nan], np.full(49, 0.001)], "NaN"),
    (np.random.default_rng(1).normal(0, 0.01, 29), "fewer than 30"),
])
def test_bootstrap_refuses_unusable_series(x, reason):
    r = bootstrap.evaluate(x, 10_000, 200, 1)
    assert r["status"] == "NOT_COMPUTED" and reason in r["reason"]


@pytest.mark.parametrize("big", [-0.8, 0.8])
def test_extreme_single_day_stays_finite(big):
    x = np.random.default_rng(4).normal(0.0005, 0.003, 300)
    x[150] = big  # one enormous day
    r = bootstrap.evaluate(x, 10_000, 300, 5)
    assert r["status"] == "OK"
    lo, hi = r["stationary"]["sharpe_ci"]
    assert math.isfinite(lo) and math.isfinite(hi) and hi - lo > 0
    sr, g3, g4 = x.mean() / x.std(ddof=1), skew(x), kurtosis(x, fisher=False)
    d = dsr.deflated(sr, 300, g3, g4, dsr.sr0(1, None), x)
    plain = dsr.deflated(sr, 300, 0.0, 3.0, dsr.sr0(1, None), x)
    assert d["dsr"] is not None and math.isfinite(d["dsr"])
    if big < 0:
        assert d["dsr"] < plain["dsr"]  # a huge loss: negative skew and fat tails pull PSR/DSR down
    else:
        # a huge win: the positive skew term RAISES PSR/DSR (a property of the formula, recorded as a caution in
        # docs/metrics_definitions.md; the bootstrap interval shows the fragility instead)
        assert d["dsr"] > plain["dsr"]


def test_unstable_sharpe_interval_is_null():
    x = np.zeros(60)
    x[30] = 0.01  # nearly all resamples miss the one non-zero day
    r = bootstrap.evaluate(x, 10_000, 300, 2)
    assert r["status"] == "OK"
    assert r["stationary"]["sharpe_ci"] is None and "unstable" in r["stationary"]["sharpe_ci_reason"]


def test_participation_ratio_reference():
    r = np.random.default_rng(0)
    f = r.standard_normal((300, 1))
    one_factor = 0.99 * f + 0.01 * r.standard_normal((300, 20))
    assert dsr.participation_ratio(one_factor)["value"] == pytest.approx(1.0, abs=0.05)
    assert dsr.participation_ratio(r.standard_normal((300, 20)))["value"] > 15
    assert dsr.participation_ratio(np.zeros((10, 3)))["value"] is None
