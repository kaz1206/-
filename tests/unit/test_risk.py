"""P9 engine and sizing (P9_PLAN §7.2)."""

import json
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import beta

from robustlab.core.models import RiskProfile
from robustlab.risk import engine as eng
from robustlab.risk import sizing as sz
from robustlab.risk import snapshot as snp

H = 252


def brute(X, D, P, k, lim=0.2, ruin=0.5, so=50.0, margin=0.02):
    e_close = 1 + k * np.cumsum(X, axis=1)
    prev = np.concatenate([np.ones((X.shape[0], 1)), e_close[:, :-1]], axis=1)
    low = prev + k * (np.minimum(0, X) + D)
    peak = np.maximum(np.maximum.accumulate(prev, axis=1), 1.0)
    return (int((((peak - low) / peak).max(axis=1) >= lim).sum()), int((low <= 1 - ruin).any(axis=1).sum()),
            int((low <= so / 100 * k * margin * P).any(axis=1).sum()))


def test_critical_lots_equal_brute_force():
    rng = np.random.default_rng(1)
    for _ in range(10):
        X = rng.normal(rng.uniform(-0.1, 0.2) * 0.005, 0.005, (200, H))
        D = -np.abs(rng.normal(0, 0.0025, (200, H)))
        P = (rng.random((200, H)) < 0.7).astype(float)
        cd, cr = eng.crit_dd(X, D, 0.2), eng.crit_ruin(X, D, 0.5)
        cs = eng.crit_stop_out(X, D, P, so_percent=50.0, margin=0.02)
        for k in np.linspace(0.2, 40, 25):
            assert (int((cd <= k).sum()), int((cr <= k).sum()), int((cs <= k).sum())) == brute(X, D, P, k)


def test_stop_out_money_mode_is_a_floor_on_equity():
    rng = np.random.default_rng(2)
    X = rng.normal(0, 0.005, (300, H))
    D = np.zeros_like(X)
    cm = eng.crit_stop_out(X, D, np.ones_like(X), so_money_frac=0.3)
    np.testing.assert_allclose(cm, eng.crit_ruin(X, D, 0.7))


@pytest.mark.parametrize("n,p", [(1000, 0.01), (2000, 0.05), (10000, 0.01), (10000, 0.05)])
def test_clopper_pearson_count(n, p):
    c = eng.c_max(n, p)
    assert beta.ppf(0.95, c + 1, n - c) <= p < beta.ppf(0.95, c + 2, n - c - 1)


def test_allowed_lot_keeps_the_upper_bound_below_the_limit():
    rng = np.random.default_rng(3)
    crit = rng.exponential(1.0, 5000)
    for p in (0.01, 0.05):
        k = eng.allowed(crit, p)
        cnt = int((crit <= k).sum())
        assert eng.upper_bound(cnt, len(crit)) <= p
        assert eng.upper_bound(int((crit <= k * 1.001).sum()), len(crit)) > p or k == np.inf


def test_scenarios_reproducible_and_blocks_stay_in_their_period():
    rng = np.random.default_rng(4)
    seg = (120, 80, 40)
    x = rng.normal(0.5, 10, sum(seg))
    h = eng.History(x=x, d=-np.abs(rng.normal(0, 3, sum(seg))), c=rng.poisson(0.5, sum(seg)).astype(float),
                    pos=np.ones(sum(seg)), seg_lens=seg, trades=rng.normal(0.5, 10, 300))
    a = {n: X for n, X, _, _ in eng.scenarios(h, np.random.default_rng(9), 500, H, 1.0)}
    b = {n: X for n, X, _, _ in eng.scenarios(h, np.random.default_rng(9), 500, H, 1.0)}
    assert list(a) == ["S0", "S1b", "S1a", "S2", "S3", "S4", "S5", "S6", "S63", "SC"]
    for n in a:
        np.testing.assert_array_equal(a[n], b[n])
    # with (almost) no voluntary jumps, a block reaching a period's last day must jump; it lands on the next
    # period's first day only by chance (about 1 / 240), never by continuing the block
    idx = eng.sb_index(seg, 1e9, 2000, H, np.random.default_rng(5))
    for e in np.cumsum(seg)[:-1]:
        at_last = idx[:, :-1] == e - 1
        assert at_last.sum() > 100
        assert (idx[:, 1:][at_last] == e).mean() < 0.03
    np.testing.assert_allclose(a["S6"].mean(), a["S0"].mean() - x.mean(), atol=1e-9)
    np.testing.assert_allclose(a["S63"], eng._ar(a["S6"], 0.0, 0.3))


@pytest.mark.parametrize("target,expected", [
    (0.07, "0.07"), (0.06999999999999999, "0.07"), (0.0699999, "0.06"), (0.0700001, "0.07"),
    (0.01, "0.01"), (0.0099999, "0"), (0.0, "0"), (5.0, "2.00"), (0.155, "0.15"),
])
def test_floor_to_step(target, expected):
    assert sz.floor_to_step(target, 0.01, 0.01, 2.0) == Decimal(expected)


def test_floor_with_coarse_step_and_odd_minimum():
    assert sz.floor_to_step(0.29, 0.1, 0.1, 100) == Decimal("0.2")
    assert sz.floor_to_step(0.14, 0.05, 0.02, 100) == Decimal("0.13")
    assert sz.floor_to_step(0.049, 0.05, 0.02, 100) == 0


def _size(**kw):
    base = dict(equity=10000.0, lot_safe=1.0, safe_binding="S63:dd", mu=5.0, var=1e4, trade_loss_q99=100.0,
                margin_per_lot=1000.0, max_positions=1.0, volume_min=0.01, volume_step=0.01, volume_max=100.0,
                kelly_fraction=0.5, cap_per_trade=0.01, margin_budget=0.3, confidence_weight=0.5, ramp_up=0.25)
    return sz.size(**(base | kw))


def test_never_rounded_up_to_the_minimum_lot():
    rng = np.random.default_rng(6)
    for _ in range(2000):
        s = _size(lot_safe=float(rng.uniform(0, 0.5)), equity=float(rng.uniform(100, 1e6)),
                  volume_min=float(rng.choice([0.01, 0.1, 1.0])), volume_step=float(rng.choice([0.01, 0.1])))
        assert s.lot <= s.lot_target + 1e-12
        assert (s.lot == 0) == (s.outcome != sz.ALLOCATED)
    s = _size(lot_safe=0.03)  # target 0.03 * 0.125 = 0.00375 < 0.01
    assert s.outcome == sz.INFEASIBLE_MIN_LOT and s.lot == 0 and s.notes["equity_needed_for_min_lot"] > 10000


def test_binding_constraints_and_outcomes():
    s = _size()
    assert s.binding == "safe" and s.outcome == sz.ALLOCATED and s.lot == 0.12
    assert _size(mu=0.0).outcome == sz.NO_ALLOCATION_MU_LCB
    m = _size(margin_per_lot=1e9)
    assert m.binding == "margin" and m.outcome == sz.MARGIN_BUDGET_EXCEEDED
    assert _size(trade_loss_q99=1000.0, lot_safe=5.0).binding == "cap"
    assert _size(mu=0.001, lot_safe=5.0).binding == "kelly"


def test_profile_cannot_relax_or_round_up():
    with pytest.raises(ValueError):
        RiskProfile(ramp_up=1.5)
    with pytest.raises(ValueError):
        RiskProfile(spread_factor=0.5)
    with pytest.raises(ValueError):
        RiskProfile(round_up=True)  # no such setting exists
    assert RiskProfile().max_dd == 0.2


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(login=12345678, name="Holder", server="Broker-Demo", currency="USD", leverage=100,
                               margin_so_mode=0, margin_so_call=100.0, margin_so_so=50.0, equity=10000.0, margin_mode=2)

    def symbol_info(self, s):
        return SimpleNamespace(visible=True, volume_min=0.01, volume_step=0.01, volume_max=100.0,
                               trade_contract_size=100000.0, currency_profit="USD", currency_margin="EUR", digits=5,
                               point=0.00001)

    def symbol_select(self, s, on):
        return True

    def symbol_info_tick(self, s):
        return SimpleNamespace(bid=1.1, ask=1.1001)

    def order_calc_margin(self, a, s, v, p):
        return 1100.0 if a == 0 else 1099.9

    def order_calc_profit(self, a, s, v, p0, p1):
        return round((p1 - p0) * 100000 * v, 2)


def test_snapshot_keeps_only_allowed_fields(tmp_path):
    snap = snp.take(FakeMT5(), "EURUSD", 0.0001)
    text = json.dumps(snap)
    assert "12345678" not in text and "Holder" not in text and "Broker-Demo" not in text
    assert snap["margin_per_lot"] == 1100.0 and snap["stress_cost_per_lot"] == 10.0
    digest = snp.save(snap, tmp_path / "s.json")
    assert snp.load(tmp_path / "s.json") == (snap, digest)
    snap["account"]["login"] = 1
    with pytest.raises(snp.SnapshotError):
        snp.validate(snap)


def test_snapshot_waits_for_the_first_price(monkeypatch):
    class Late(FakeMT5):
        calls = 0

        def symbol_info_tick(self, s):
            Late.calls += 1
            return None if Late.calls < 3 else SimpleNamespace(bid=1.1, ask=1.1001)

    monkeypatch.setattr(snp, "TICK_POLL_SEC", 0.0)
    assert snp.take(Late(), "EURUSD", 0.0001)["price"]["ask"] == 1.1001

    class Never(FakeMT5):
        def symbol_info_tick(self, s):
            return SimpleNamespace(bid=0.0, ask=0.0)

    monkeypatch.setattr(snp, "TICK_WAIT_SEC", 0.0)
    with pytest.raises(snp.SnapshotError, match="Market Watch"):
        snp.take(Never(), "EURUSD", 0.0001)
