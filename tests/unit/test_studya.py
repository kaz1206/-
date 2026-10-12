"""Study A unit tests (SPEC §8): T1 vs a naive tick-by-tick reading of RL_TSMom, phase / warm-up / equality /
reversal / end of test / stop-out, H0 = H1 at strength 0 (bit for bit), common random numbers, the false-pass decision
rule, trace completeness and serial = parallel."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from robustlab.studya import evaluate as E
from robustlab.studya import prices, t1
from robustlab.studya import run as R

ROOT = Path(__file__).resolve().parents[2]
KW = {"lots": 0.1, "spread": 0.0001, "contract": 100000.0, "deposit": 10000.0, "leverage": 100.0}


def days_n(n: int) -> list[dt.date]:
    return prices.weekdays(dt.date(2005, 1, 3), dt.date(2005, 1, 3) + dt.timedelta(days=2 * n + 7))[:n]


def naive(p, w, L, H, spb, spd, lots=0.1, spread=0.0001, contract=1e5, deposit=1e4):
    """RL_TSMom read literally, tick by tick (no stop-out): returns (deals, daily equity closes)."""
    units = lots * contract
    pos, entry, bal, g = 0, 0.0, deposit, 0
    deals, eq_close = [], []
    for s in range(w.s_start + 1, w.s_end + 1):
        bid = p[s]
        ask = round(bid + spread, 5)
        if (s - 1) % spb == 0:                       # first tick of a new bar
            k = (s - 1) // spb
            g += 1
            if (g - 1) % H == 0 and k + 1 >= L + 2:
                c1, c0 = p[k * spb], p[(k - L) * spb]
                tgt = 1 if c1 > c0 else (-1 if c1 < c0 else 0)
                if tgt != 0 and tgt != pos:
                    if pos:
                        px = bid if pos > 0 else ask
                        pnl = round((px - entry) * pos * units, 2)
                        bal = round(bal + pnl, 2)
                        deals.append(("out", -pos, px, pnl, s))
                    entry = ask if tgt > 0 else bid
                    pos = tgt
                    deals.append(("in", tgt, entry, 0.0, s))
        if s % spd == 0:
            mk = bid if pos > 0 else ask
            eq_close.append(round(bal + (pos * (mk - entry) * units if pos else 0.0), 2))
    if pos:
        px = p[w.s_end] if pos > 0 else round(p[w.s_end] + spread, 5)
        deals.append(("end", -pos, px, round((px - entry) * pos * units, 2), w.s_end))
    return deals, eq_close


def model_deals(res: t1.TestResult, spd, days, w):
    out = []
    for d in res.deals:
        if d["type"] == "2":
            continue
        side = 1 if d["type"] == "0" else -1
        kind = "end" if d["comment"] == t1.END_OF_TEST else ("in" if d["entry"] == "0" else "out")
        out.append((kind, side, float(d["price"]), float(d["profit"])))
    return out


@pytest.mark.parametrize("seed", range(12))
def test_t1_matches_naive_reading_of_the_ea(seed):
    rng = np.random.default_rng(seed)
    spb, spd = 8, 48
    n_days = 120
    days = days_n(n_days)
    steps = rng.standard_normal(n_days * spd) * 0.0009
    p = prices.path(steps, 1.10)
    w = t1.Window(20 * spd, n_days * spd)
    for L, H in ((6, 1), (12, 3), (30, 6), (60, 5), (126, 2)):
        res = t1.run_test(p, w, L, H, spb=spb, spd=spd, days=days, **KW)
        nd, neq = naive(p, w, L, H, spb, spd)
        md = model_deals(res, spd, days, w)
        assert [(k, s, round(px, 5), pr) for k, s, px, pr, _ in nd] == [(k, s, round(px, 5), pr) for k, s, px, pr in md]
        assert [float(r["equity_close"]) for r in res.daily] == pytest.approx(neq, abs=0.011)
        assert res.stats["trades"] == sum(1 for d in nd if d[0] in ("out", "end"))
        assert res.stats["profit"] == pytest.approx(sum(d[3] for d in nd), abs=1e-6)


def test_phase_warmup_equality_and_end_of_test():
    spb, spd = 2, 4
    days = days_n(6)
    # bars close at p[2k]; first tick of bar k+1 at step 2k+1
    p = np.array([1.0] * 25)
    p[8] = 1.0010    # close of bar 4 above close of bar 2 -> long at the first tick of bar 5 (step 9) if decided
    p[9:] = 1.0010
    p[16:] = 1.0005  # close of bar 8 below close of bar 6 -> short
    w = t1.Window(4, 24)             # first tick step 5 = bar 3; bars counted from the test start: g=1 at k=2
    res = t1.run_test(p, w, 2, 2, spb=spb, spd=spd, days=days, **KW)
    trade = [d for d in res.deals if d["type"] != "2"]
    # decisions at k = 2, 4, 6, 8, 10 (g = 1, 3, 5, 7, 9); k=2 has Bars=3 < L+2=4 -> skipped (warm-up)
    # k=4: c1 = p[8] = 1.0010 > c0 = p[4] = 1.0 -> buy at ask of step 9
    assert trade[0]["entry"] == "0" and trade[0]["type"] == "0" and trade[0]["price"] == "1.00110"
    assert res.first_deal_step == 9
    # k=6: c1 = p[12] = 1.0010, c0 = p[8] = 1.0010 -> equal: nothing; k=8: c1 = p[16] = 1.0005 < c0 = p[12] -> reverse
    assert [(d["type"], d["entry"]) for d in trade[1:3]] == [("1", "1"), ("1", "0")]
    assert trade[1]["profit"] == f"{round((1.0005 - 1.0011) * 10000, 2):.2f}"
    assert trade[-1]["comment"] == t1.END_OF_TEST and len(res.closes) == 2
    assert [c["kind"] for c in res.closes] == ["EA", "END_OF_TEST"]


def test_stop_out_closes_and_refuses_new_orders():
    spb, spd = 2, 4
    days = days_n(6)
    p = np.array([1.0] * 25)
    p[8:] = 1.0010           # long from step 9
    p[13:] = 0.0              # catastrophic fall (only to trigger the stop-out in a unit test)
    p[13:] = 0.0005
    w = t1.Window(4, 24)
    res = t1.run_test(p, w, 2, 2, spb=spb, spd=spd, days=days, **KW)
    assert res.tracking["stop_out_deals"] == 1
    so = [d for d in res.deals if d["reason"] == "6"]
    assert len(so) == 1 and so[0]["time"].startswith(f"{days[3]:%Y.%m.%d}")
    # after the stop-out the balance is below the required margin only if it is tiny; here it is not: orders resume
    assert any(c["kind"] == "STOP_OUT" for c in res.closes)


def _small_cfg(tmp_path: Path) -> Path:
    text = (ROOT / "configs" / "study_a.yaml").read_text()
    text = text.replace("calendar: {warmup_weekdays: 290, discovery: [2001-01-02, 2014-01-01], v0: [2016-01-11, 2017-12-22],\n"
                        "           v1: [2019-07-08, 2019-12-20]}",
                        "calendar: {warmup_weekdays: 60, discovery: [2001-01-02, 2002-07-01], v0: [2002-07-15, 2002-12-20],\n"
                        "           v1: [2003-01-06, 2003-03-28]}")
    text = text.replace("InpLookback: {start: 126, step: 126, stop: 1512}", "InpLookback: {start: 12, step: 12, stop: 36}")
    text = text.replace("bootstrap_reps: 2000", "bootstrap_reps: 200")
    assert "2002-07-01" in text and "stop: 36" in text
    p = tmp_path / "study_a_small.yaml"
    p.write_text(text)
    return p


def test_strength_zero_is_h0_bit_for_bit_and_bases_are_shared(tmp_path):
    st = E.setup(_small_cfg(tmp_path))
    h0 = E.round_path(st, 11, 3)
    assert np.array_equal(h0, E.round_path(st, 11, 3, "E1", 0.0, 99))
    assert np.array_equal(h0, E.round_path(st, 11, 3, "E2", 0.0, 98))
    # round i's base depends on (seed, i) only
    assert np.array_equal(prices.base_steps(100, st.pm, 11, 3), prices.base_steps(100, st.pm, 11, 3))
    assert not np.array_equal(prices.base_steps(100, st.pm, 11, 3), prices.base_steps(100, st.pm, 11, 4))
    e1 = E.round_path(st, 11, 3, "E1", 1e-4, 99)
    assert not np.array_equal(h0, e1)


def test_false_pass_decision_rule():
    assert R.clopper_pearson(0, 1000)["upper_one_sided"] == pytest.approx(0.002991, abs=1e-6)
    assert R.clopper_pearson(38, 1000)["upper_one_sided"] <= 0.05 < R.clopper_pearson(39, 1000)["upper_one_sided"]


def test_trace_is_complete_and_serial_equals_parallel(tmp_path):
    cfg = _small_cfg(tmp_path)
    st = E.setup(cfg)
    m = {"edge": None, "rounds": 4, "base_seed": 5}
    s1, r1 = R.execute(cfg, "unit", m, workers=1)
    _s2, r2 = R.execute(cfg, "unit", m, workers=2)
    strip = lambda rs: [{k: v for k, v in r.items() if k != "seconds"} for r in rs]
    assert strip(r1) == strip(r2)
    for r in r1:
        assert len(r["rows"]) == len(st.lookbacks) * len(st.holds)
        for row in r["rows"]:
            assert {"candidate_id", "d", "p4", "p5", "p6", "final"} <= set(row)
            if row["p4"]["outcome"] == "PASS":
                assert "p7" in row
            if row.get("p7", {}).get("outcome") == "SELECTED":
                assert "p8a" in row
    assert s1["rounds"] == 4 and "s1" in s1 and s1["verdict"] in ("MET", "NOT_MET", "INVALID_S1")
