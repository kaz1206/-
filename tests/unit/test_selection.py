"""P7 gates, R3 neighbourhood counting, integrated ranking and representatives (P7_PLAN §3, §6.2)."""

import itertools
import math
import random

import numpy as np
import pytest

from robustlab.selection import core


def grid(shape, fill=0.02):
    return np.full(shape, fill), np.full(shape, "OK", dtype=object)


def g_by(gs, name):
    return next(g for g in gs if g.gate == name)


# ----- R3: neighbourhood counting -----

def test_no_trade_neighbours_are_not_counted_but_ruin_is():
    s, st = grid((5, 5))
    st[1, 1] = "NO_TRADES"
    s[1, 1] = np.nan
    st[1, 2] = "NO_TRADES"
    s[1, 2] = np.nan
    nb = core.neighbourhood((2, 2), s, st)
    assert (nb.counted, nb.no_trade, nb.ruin) == (6, 2, 0)
    assert nb.robust_score == pytest.approx(0.02)
    st[3, 3] = "BANKRUPTCY"
    nb = core.neighbourhood((2, 2), s, st)
    assert (nb.counted, nb.ruin) == (6, 1)
    gs = core.gates(0.02, "IN_REGION", nb, 2, 1.5, 0.0)
    assert g_by(gs, "neighbour_ruin").status == core.FAIL and "NEIGHBORHOOD_UNSTABLE" in core.reasons_of(gs)


def test_not_computable_neighbours_count_as_minus_inf():
    s, st = grid((3, 3))
    st[0, 0] = "NOT_COMPUTABLE"
    nb = core.neighbourhood((1, 1), s, st)
    assert (nb.counted, nb.not_computable, nb.ruin) == (8, 1, 0)
    assert nb.robust_score == pytest.approx(0.02)  # 1 of 9 values is -inf: not in the 25% quantile
    for c in [(0, 1), (0, 2), (1, 0)]:
        st[c] = "NOT_COMPUTABLE"
    assert core.neighbourhood((1, 1), s, st).robust_score is None  # 4 of 9 are -inf


@pytest.mark.parametrize(("d", "floor"), [(1, 1), (2, 4), (3, 13)])
def test_neighbour_floor(d, floor):
    assert core.neighbour_floor(d) == floor


def test_floor_corner_fails_edge_passes():
    s, st = grid((5, 5))
    corner = core.gates(0.02, "IN_REGION", core.neighbourhood((0, 0), s, st), 2, 1.5, 0.0)
    assert g_by(corner, "neighbour_count").status == core.FAIL
    assert core.reasons_of(corner) == ["NEIGHBORHOOD_INSUFFICIENT"]
    edge = core.gates(0.02, "IN_REGION", core.neighbourhood((0, 2), s, st), 2, 1.5, 0.0)
    assert g_by(edge, "neighbour_count").value == 5 and core.reasons_of(edge) == []


def test_idle_triangle_boundary_is_evaluated_and_surrounded_cell_is_not():
    # fast (axis 0) >= slow (axis 1) trades nothing, as the P3 EA does
    s, st = grid((6, 6))
    for i, j in itertools.product(range(6), range(6)):
        if i >= j:
            s[i, j], st[i, j] = np.nan, "NO_TRADES"
    nb = core.neighbourhood((1, 2), s, st)  # just above the diagonal
    assert nb.no_trade == 3 and nb.counted == 5
    assert core.reasons_of(core.gates(0.02, "IN_REGION", nb, 2, 1.5, 0.0)) == []
    # P5's standard robust score treats the idle cells as -inf; R3 does not (both are recorded by run.py)
    nb = core.neighbourhood((4, 5), s, st)  # top edge next to the diagonal: 2 counted
    assert nb.counted == 2
    assert "NEIGHBORHOOD_INSUFFICIENT" in core.reasons_of(core.gates(0.02, "IN_REGION", nb, 2, 1.5, 0.0))


def test_missing_cells_are_outside_the_grid():
    s, st = grid((3, 3))
    s[0, 0], st[0, 0] = np.nan, ""
    assert core.neighbourhood((1, 1), s, st).counted == 7


# ----- gates -----

def test_peak_ratio_and_median_not_positive():
    s, st = grid((3, 3))
    s[1, 1] = 0.1
    gs = core.gates(0.1, "IN_REGION", core.neighbourhood((1, 1), s, st), 2, 1.5, 0.0)
    assert g_by(gs, "peak_ratio").value == pytest.approx(5.0) and "ISOLATED_PEAK" in core.reasons_of(gs)
    s[:] = -0.01
    s[1, 1] = 0.03
    gs = core.gates(0.03, "IN_REGION", core.neighbourhood((1, 1), s, st), 2, 1.5, 0.0)
    pk = g_by(gs, "peak_ratio")
    assert pk.status == core.FAIL and pk.value is None and "<= 0" in pk.note


def test_every_gate_is_evaluated_and_reasons_are_unique():
    s, st = grid((3, 3), fill=-0.01)
    st[0, 1] = "BANKRUPTCY"
    s[0, 0] = 0.05
    gs = core.gates(0.05, "ISOLATED_PASS", core.neighbourhood((0, 0), s, st), 2, 1.5, 0.0)
    assert len(gs) == 5 and all(g.status in (core.PASS, core.FAIL, core.NOT_EVALUATED) for g in gs)
    assert core.reasons_of(gs) == ["ISOLATED_PEAK", "NEIGHBORHOOD_INSUFFICIENT", "NEIGHBORHOOD_UNSTABLE"]


def test_robust_score_not_positive_fails():
    s, st = grid((3, 3), fill=-0.001)
    s[1, 1] = 0.001
    gs = core.gates(0.001, "IN_REGION", core.neighbourhood((1, 1), s, st), 2, 1.5, 0.0)
    assert g_by(gs, "robust_score").status == core.FAIL


def test_gates_never_take_dsr():
    import inspect
    assert "dsr" not in inspect.signature(core.gates).parameters
    assert not core.DSR_CODES & {c for c, (_, _, sev) in core.REASON_CODES.items() if sev == "FAIL"}


# ----- integrated ranking (R4, R5) -----

R = core.RankInput
HAND = [R("cd_a", 0.10, 0.5, 100.0), R("cd_b", 0.08, 0.9, 120.0), R("cd_c", 0.05, 0.3, 200.0),
        R("cd_d", 0.10, 0.5, 100.0)]


def test_ranking_by_hand():
    out = core.rank(HAND)
    assert [r.candidate_id for r in out] == ["cd_a", "cd_d", "cd_b", "cd_c"]
    by = {r.candidate_id: r for r in out}
    assert [by[c].layer for c in ("cd_a", "cd_b", "cd_c", "cd_d")] == [1, 1, 2, 1]
    assert by["cd_a"].ranks == {"robust_score": 1.5, "dsr": 2.5, "max_dd_p95": 1.5}
    assert by["cd_a"].mean_rank == pytest.approx((1.5 + 2.5 + 1.5) / 3)
    assert by["cd_b"].mean_rank == pytest.approx((3 + 1 + 3) / 3)
    sel = core.select(HAND, 2)
    assert sel.selected == ["cd_a", "cd_d"]
    assert sel.not_selected == {"cd_b": "NOT_SELECTED_BUDGET", "cd_c": "DOMINATED"}


def test_lower_max_dd_is_better():
    out = core.rank([R("cd_x", 0.1, 0.5, 300.0), R("cd_y", 0.1, 0.5, 100.0)])
    assert [r.candidate_id for r in out] == ["cd_y", "cd_x"] and out[1].layer == 2


def test_tie_break_order_robust_then_dd_then_dsr_then_id():
    # same layer, same mean rank: x is best on robust, y best on dd, z best on dsr
    items = [R("cd_z", 0.1, 0.9, 200.0), R("cd_y", 0.2, 0.5, 100.0), R("cd_x", 0.3, 0.7, 300.0)]
    out = core.rank(items)
    assert {r.layer for r in out} == {1} and len({r.mean_rank for r in out}) == 1
    assert [r.candidate_id for r in out] == ["cd_x", "cd_y", "cd_z"]
    # equal robust ranks and equal mean rank -> max DD decides before DSR
    out = core.rank([R("cd_p", 0.1, 0.9, 200.0), R("cd_q", 0.1, 0.5, 100.0)])
    assert out[0].mean_rank == out[1].mean_rank and [r.candidate_id for r in out] == ["cd_q", "cd_p"]


def test_scale_and_monotone_transforms_do_not_change_the_order():
    rng = np.random.default_rng(7)
    items = [R(f"cd_{i:02d}", float(rng.uniform(0.01, 0.2)), float(rng.uniform(0.01, 0.99)),
               float(rng.uniform(50, 500))) for i in range(25)]
    base = [r.candidate_id for r in core.rank(items)]
    t = [R(r.candidate_id, r.robust_score * 1000, r.dsr ** 2, r.max_dd_p95 * 7 + 3) for r in items]
    assert [r.candidate_id for r in core.rank(t)] == base
    # DSR only through its rank: a monotone transform of DSR alone changes nothing (R4)
    t = [R(r.candidate_id, r.robust_score, math.sqrt(r.dsr), r.max_dd_p95) for r in items]
    assert [r.candidate_id for r in core.rank(t)] == base


def test_missing_inputs_are_worst_and_flagged():
    out = core.rank([R("cd_m", 0.2, None, 100.0), R("cd_n", 0.1, 0.1, 100.0)])
    by = {r.candidate_id: r for r in out}
    assert by["cd_m"].missing == ["dsr"] and by["cd_m"].ranks["dsr"] == 2


def test_rounding_removes_float_noise():
    out = core.rank([R("cd_b", 0.1 + 1e-13, 0.5, 100.004), R("cd_a", 0.1, 0.5, 100.0)])
    assert out[0].mean_rank == out[1].mean_rank and [r.candidate_id for r in out] == ["cd_a", "cd_b"]


def test_over_budget_selection_is_reproducible_under_reordering():
    rng = random.Random(11)
    items = []
    for i in range(40):  # many exact ties on purpose
        items.append(R(f"cd_{i:02d}", rng.choice([0.05, 0.08, 0.1]), rng.choice([0.2, 0.5, 0.8]),
                       rng.choice([100.0, 150.0, 200.0])))
    ref = core.select(items, 5)
    for _ in range(20):
        rng.shuffle(items)
        s = core.select(items, 5)
        assert s.selected == ref.selected and s.not_selected == ref.not_selected
        assert [r.candidate_id for r in s.ranking] == [r.candidate_id for r in ref.ranking]
    assert len(ref.selected) == 5


def test_representative_is_the_closest_gate_pass_to_the_medoid():
    idx = {"cd_m": (2, 2), "cd_a": (1, 2), "cd_b": (3, 3), "cd_c": (0, 0)}
    ri = {c: R(c, 0.1, 0.5, 100.0) for c in idx}
    assert core.representative(["cd_a", "cd_b", "cd_c", "cd_m"], (2, 2), idx, ri) == "cd_m"
    # medoid failed a gate: both cd_a and cd_b are at distance 1 -> integrated rank, then candidate_id
    assert core.representative(["cd_a", "cd_b", "cd_c"], (2, 2), idx, ri) == "cd_a"
    ri["cd_b"] = R("cd_b", 0.2, 0.5, 100.0)
    assert core.representative(["cd_a", "cd_b", "cd_c"], (2, 2), idx, ri) == "cd_b"
    assert core.representative([], (2, 2), idx, ri) is None
