"""PSR / MinTRL (R PerformanceAnalytics formulas) and the initial-filter gates (P4_PLAN §6)."""

import math
from statistics import NormalDist

import numpy as np
import pytest

from robustlab.core.models import StrategyProfile
from robustlab.filtering import gates
from robustlab.metrics.engine import daily_metrics
from robustlab.stats.psr import min_trl, psr

# R example (ProbSharpeRatio.R / MinTRL.R, PerformanceAnalytics 2.1.0): monthly, n = 59
R_SR, R_REF, R_SK, R_KR, R_N = 2 / 12 ** 0.5, 1 / 12 ** 0.5, -0.72, 5.78, 59


def r_psr(sr, ref, sk, kr, n):
    """The R formula, written independently (statistics.NormalDist instead of scipy)."""
    return NormalDist().cdf((sr - ref) * (n - 1) ** 0.5 / (1 - sr * sk + sr ** 2 * (kr - 1) / 4) ** 0.5)


def test_psr_matches_the_r_formula_on_the_r_example():
    got = psr(R_SR, R_N, R_SK, R_KR, R_REF).value
    assert got == pytest.approx(r_psr(R_SR, R_REF, R_SK, R_KR, R_N), abs=1e-12)
    assert got == pytest.approx(0.9487, abs=1e-4)
    # R's default ignore_kurtosis=TRUE (kr = 3) gives a higher, less conservative value (C54)
    assert r_psr(R_SR, R_REF, R_SK, 3.0, R_N) > got


def test_min_trl_matches_the_r_formula_and_is_consistent_with_psr():
    t = min_trl(R_SR, R_SK, R_KR, R_REF, 0.95).value
    expected = 1 + (1 - R_SK * R_SR + (R_KR - 1) / 4 * R_SR ** 2) * (NormalDist().inv_cdf(0.95) / (R_SR - R_REF)) ** 2
    assert t == pytest.approx(expected, rel=1e-12)
    assert t == pytest.approx(59.90, abs=0.01)
    # PSR crosses 0.95 exactly at MinTRL
    assert psr(R_SR, math.ceil(t), R_SK, R_KR, R_REF).value >= 0.95 > psr(R_SR, math.floor(t), R_SK, R_KR, R_REF).value


def test_psr_properties():
    assert psr(0.1, 100, 0.0, 3.0, 0.1).value == pytest.approx(0.5)  # observed == benchmark
    assert psr(0.05, 1000, 0, 3).value > psr(0.05, 100, 0, 3).value  # longer record -> closer to 1
    assert psr(0.05, 250, 0, 9).value < psr(0.05, 250, 0, 3).value  # fatter tails -> lower
    normal = NormalDist().cdf(0.05 * 249 ** 0.5 / (1 + 0.05 ** 2 / 2) ** 0.5)
    assert psr(0.05, 250, 0.0, 3.0).value == pytest.approx(normal)  # gamma3 = 0, gamma4 = 3


@pytest.mark.parametrize(("sr", "n", "sk", "kr"), [
    (None, 100, 0, 3), (0.1, None, 0, 3), (0.1, 1, 0, 3), (float("nan"), 100, 0, 3),
    (0.1, 100, float("inf"), 3), (0.1, 100, 0, None), (3.0, 100, 2.0, 1.0),  # non-positive variance term
])
def test_psr_invalid_inputs_give_none_with_a_reason(sr, n, sk, kr):
    r = psr(sr, n, sk, kr)
    assert r.value is None and r.reason


def test_min_trl_is_none_when_the_benchmark_is_not_exceeded():
    assert min_trl(0.0, 0, 3).value is None and min_trl(-0.1, 0, 3).value is None


def test_psr_does_not_depend_on_the_lot():
    rng = np.random.default_rng(20261011)  # seed recorded (CLAUDE.md)
    pnl = rng.normal(0.4, 6.0, 300)
    d = 10_000.0
    stats = []
    for k in (1, 10):
        eq = d + np.cumsum(k * pnl)
        m, na = {}, {}
        daily_metrics([{"equity_close": str(x)} for x in eq], d, m, na)
        stats.append(psr(m["daily_sharpe"], m["days"], m["daily_skewness"], m["daily_kurtosis"]).value)
    assert stats[0] == pytest.approx(stats[1], rel=1e-9)


# --- gates ---------------------------------------------------------------------------------------
PROFILE = StrategyProfile(psr_threshold=0.95, min_trades=30, min_days=60, min_margin_level_pct=200,
                          trades_per_year=(100, 600))


def good(**over):
    m = {"bankrupt": False, "stop_out_deals": 0, "min_margin_level": 5000.0, "max_positions": 1, "trades": 300,
         "days": 520, "net_profit": 300.0, "daily_sharpe": 0.15, "daily_skewness": -0.1, "daily_kurtosis": 4.0,
         "equity_max_dd": 100.0, "equity_max_dd_pct": 1.0, "balance_max_dd": 90.0}
    m.update(over)
    return m


def by_gate(d):
    return {g.gate: g for g in d.gates}


def test_a_good_candidate_passes_every_gate():
    d = gates.evaluate(good(), PROFILE, 2.0)
    assert d.outcome == "PASS" and d.reasons == [] and d.warnings == []
    assert len(d.gates) == 9 and d.values["psr"] > 0.95 and d.values["min_trl"] < 520


def test_every_gate_is_evaluated_even_after_the_first_failure():
    d = gates.evaluate(good(bankrupt=True, stop_out_deals=3, min_margin_level=50.0, trades=5, days=10,
                            net_profit=-5.0, daily_sharpe=-0.1), PROFILE, 2.0)
    assert d.outcome == "FAIL" and len(d.gates) == 9
    assert d.reasons == ["BANKRUPTCY", "STOP_OUT", "MARGIN_LEVEL_LOW", "INSUFFICIENT_TRADES", "INSUFFICIENT_DATA",
                         "NEGATIVE_EXPECTANCY", "LOW_PSR"]


def test_an_idle_pass_never_passes():
    # fast >= slow: no trade at all (P3 runs these and counts them as trials)
    m = good(trades=0, net_profit=0.0, daily_sharpe=None, daily_skewness=None, daily_kurtosis=None,
             min_margin_level=None, max_positions=0)
    d = gates.evaluate(m, PROFILE, 2.0)
    assert d.outcome == "FAIL"
    assert {"INSUFFICIENT_TRADES", "NEGATIVE_EXPECTANCY", "LOW_PSR"} <= set(d.reasons)
    g = by_gate(d)
    assert g["psr"].value is None and "not computable" in g["psr"].note
    assert g["margin_level"].status == "NOT_APPLICABLE"


@pytest.mark.parametrize("key", ["bankrupt", "stop_out_deals", "trades", "days", "net_profit", "daily_sharpe",
                                 "daily_skewness", "daily_kurtosis"])
@pytest.mark.parametrize("bad", ["missing", None, float("nan"), float("inf"), "12"])
def test_missing_or_invalid_values_never_pass(key, bad):
    m = good()
    if bad == "missing":
        del m[key]
    else:
        m[key] = bad
    d = gates.evaluate(m, PROFILE, 2.0)
    assert d.outcome == "FAIL", (key, bad)


def test_boundaries():
    g = by_gate(gates.evaluate(good(trades=30, days=60, min_margin_level=200.0, net_profit=0.01), PROFILE, 2.0))
    assert g["trades"].status == g["days"].status == g["margin_level"].status == g["expectancy"].status == "PASS"
    g = by_gate(gates.evaluate(good(trades=29, days=59, min_margin_level=199.99, net_profit=0.0), PROFILE, 2.0))
    assert g["trades"].status == g["days"].status == g["margin_level"].status == g["expectancy"].status == "FAIL"


def test_psr_threshold_boundary_and_frequency_warning():
    # observed Sharpe == SR* gives PSR 0.5 -> fails a 0.95 threshold
    d = gates.evaluate(good(daily_sharpe=0.0), PROFILE, 2.0)
    assert by_gate(d)["psr"].value == pytest.approx(0.5) and "LOW_PSR" in d.reasons
    d = gates.evaluate(good(trades=50), PROFILE, 2.0)  # 25 per year < 100
    assert d.warnings == ["TRADE_FREQUENCY_OUT_OF_PROFILE"] and "TRADE_FREQUENCY_OUT_OF_PROFILE" not in d.reasons


def test_max_positions_limit_and_dd_is_recorded_not_filtered():
    prof = PROFILE.model_copy(update={"max_positions": 3})
    assert "MAX_POSITIONS_EXCEEDED" in gates.evaluate(good(max_positions=4), prof, 2.0).reasons
    d = gates.evaluate(good(equity_max_dd=9_000.0, equity_max_dd_pct=90.0), PROFILE, 2.0)
    assert d.outcome == "PASS" and d.values["equity_max_dd_pct"] == 90.0  # C51


def test_reason_codes_are_catalogued():
    d = gates.evaluate(good(bankrupt=True, trades=50), PROFILE, 2.0)
    for c in d.reasons + d.warnings:
        assert c in gates.REASON_CODES
