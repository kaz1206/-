"""P8a evidence p-value with per-period aggregation, Holm, costs (P8_PLAN §4.2-§4.4, §8.2)."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from robustlab.stats import validation as ev
from robustlab.validation import costs

ROOT = Path(__file__).resolve().parents[2]


def research():
    """The pre-implementation script (P8_PLAN §4.4), loaded as a module."""
    spec = importlib.util.spec_from_file_location("vg2", ROOT / "docs/research/p8_simulations/validation_gate2.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_production_p_value_equals_the_pre_implementation_check():
    """Same inputs and the same random stream give exactly the p-values of validation_gate2.py (H1-H4 hold)."""
    vg = research()
    for kind in ("iid", "ar1_0.4", "garch_ar1", "ar2", "vol2"):
        rng_a, rng_b = np.random.default_rng(5), np.random.default_rng(5)
        for _ in range(6):
            x0, x1 = vg.gen(kind, rng_a, 1, 0.0, [0.0])
            y0, y1 = vg.gen(kind, rng_b, 1, 0.0, [0.0])
            p_ref = vg.p_value([x0[:, 0], x1[:, 0]], rng_a)
            p_prod = ev.evidence([y0[:, 0], y1[:, 0]], vg.B, rng_b)["p"]
            assert p_prod == p_ref


def test_autocorrelation_uses_pairs_inside_periods_only():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([10.0, 9.0, 8.0])
    allx = np.concatenate([a, b])
    m = allx.mean()
    want = (sum((a[1:] - m) * (a[:-1] - m)) + sum((b[1:] - m) * (b[:-1] - m))) / sum((allx - m) ** 2)
    assert ev.acf_segments([a, b], 1) == pytest.approx(want)
    joined = sum((allx[1:] - m) * (allx[:-1] - m)) / sum((allx - m) ** 2)
    assert ev.acf_segments([a, b], 1) != pytest.approx(joined)  # the boundary pair (4, 10) is not used


def test_no_boundary_return_and_no_gap_zeros():
    rng = np.random.default_rng(1)
    a, b = rng.normal(0.001, 0.01, 200), rng.normal(0.001, 0.01, 60)
    e = ev.evidence([a, b], 200, np.random.default_rng(2))
    assert e["n"] == 260 and e["n_per_period"] == [200, 60]
    assert e["sharpe"] == pytest.approx(np.concatenate([a, b]).mean() / np.concatenate([a, b]).std(ddof=1))
    padded = ev.evidence([np.concatenate([a, np.zeros(30), b])], 200, np.random.default_rng(2))
    assert padded["n"] == 290 and padded["sharpe"] != pytest.approx(e["sharpe"])  # zeros would change it


def test_period_order_changes_only_the_bootstrap_noise():
    rng = np.random.default_rng(3)
    a, b = rng.normal(0.0005, 0.01, 300), rng.normal(0.0005, 0.02, 80)
    e1 = ev.evidence([a, b], 300, np.random.default_rng(4))
    e2 = ev.evidence([b, a], 300, np.random.default_rng(4))
    for k in ("standard", "ar1", "newey_west"):
        assert e1["psr"][k] == pytest.approx(e2["psr"][k])
    assert abs(e1["psr"]["bootstrap_se"] - e2["psr"]["bootstrap_se"]) < 0.05


@pytest.mark.parametrize("segs", [[np.zeros(100)], [np.ones(10) * 0.01, np.ones(10) * -0.01], [np.full(50, np.nan)]])
def test_no_evidence_gives_p_one(segs):
    e = ev.evidence(segs, 100, np.random.default_rng(0))
    assert e["p"] == 1.0 and e["reason"].startswith("no evidence")


def test_p_is_one_minus_the_smallest_psr():
    rng = np.random.default_rng(8)
    x = rng.normal(0.003, 0.01, 400)
    e = ev.evidence([x[:300], x[300:]], 300, np.random.default_rng(9))
    assert set(e["psr"]) == {"standard", "ar1", "newey_west", "bootstrap_se"}
    assert e["p"] == pytest.approx(1 - min(e["psr"].values()))


def test_holm_matches_statsmodels_and_hand_examples():
    """300 random cases here (statsmodels takes ~50 ms per call); the full 10,000 of check H2 run in
    docs/research/p8_validation/run_checks.py with the production function."""
    rng = np.random.default_rng(20261011)
    grid = np.array([0.0, 0.001, 0.0125, 0.01, 0.0166666, 0.025, 0.05, 0.0500001, 0.2, 1.0])
    for _ in range(300):
        k = int(rng.integers(1, 9))
        ps = np.where(rng.random(k) < 0.4, rng.choice(grid, k), rng.random(k) * 0.1)
        ours = ev.holm({f"cd_{i}": float(p) for i, p in enumerate(ps)})
        ref = multipletests(ps, alpha=0.05, method="holm")[0]
        assert [ours[f"cd_{i}"] for i in range(k)] == list(ref)
    assert ev.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.005}) == {"a": True, "b": False, "c": False, "d": True}
    assert ev.holm({"a": 0.049}) == {"a": True} and ev.holm({"a": 0.051}) == {"a": False}  # K = 1: plain 5%


# ----- costs (P8_PLAN §4.3) -----

def deal(entry, volume, profit=0.0, typ=1, commission=0.0, swap=0.0, fee=0.0):
    return {"type": str(typ), "entry": str(entry), "volume": str(volume), "profit": str(profit),
            "commission": str(commission), "swap": str(swap), "fee": str(fee)}


def test_extra_cost_by_hand_eurusd_and_xauusd():
    deals = [deal(0, 0.01, typ=0), deal(1, 0.01, 5.0)]  # one round trip: entry IN is not charged
    # EURUSD, USD account: 0.00010 / 0.00001 * 1.0 * 0.01 = 0.10
    assert costs.extra_cost(deals, 0.00010, 0.00001, 1.0) == pytest.approx(0.10)
    # XAUUSD, USD account (100 oz per lot): 0.30 / 0.01 * 1.0 * 0.01 = 0.30
    assert costs.extra_cost(deals, 0.30, 0.01, 1.0) == pytest.approx(0.30)
    assert costs.extra_cost(deals, 0.30, 0.01, 1.0, 0.5) == pytest.approx(0.15)


def test_cost_is_per_closing_deal_and_proportional_to_volume():
    deals = [deal(0, 0.03, typ=0), deal(1, 0.01), deal(1, 0.02), deal(2, 0.01), deal(3, 0.01),
             {"type": "2", "entry": "0", "volume": "0", "profit": "10000", "commission": "0", "swap": "0", "fee": "0"}]
    # partial closes 0.01 + 0.02, an INOUT 0.01 and an OUT_BY 0.01 are charged; the IN and the balance deal are not
    assert costs.extra_cost(deals, 0.00010, 0.00001, 1.0) == pytest.approx(10 * 0.05)


def test_profit_concentration_uses_all_deal_costs():
    ds = [deal(1, 0.01, p, commission=-0.1, swap=0.05) for p in (10, 9, 8, 7, 6, 1, -2)]
    c = costs.concentration(ds)
    assert c["closing_deals"] == 7 and c["top_sum"] == pytest.approx(40 - 0.25)
    assert c["rest_sum"] == pytest.approx(-1 - 0.1)
