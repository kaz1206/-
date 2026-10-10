"""Neighbourhood statistics, regions and sub-periods on synthetic grids (P5_PLAN §6).

The three checks asked for at approval:
  1. every candidate gets values even with 0 filter passes      -> test_no_filter_pass_still_evaluates_everyone
  2. bankruptcy and no-trades are recorded apart, counts agree   -> test_minus_inf_causes_are_kept_and_counted
  3. a plateau beats an isolated peak, the medoid is per spec    -> test_isolated_peak_scores_below_the_plateau
"""

import itertools
import math

import numpy as np
import pytest

from robustlab.robustness import grid_stats as gs
from robustlab.robustness.subperiod import yearly


def grid(shape, fill=0.01):
    scores = np.full(shape, fill)
    statuses = np.full(shape, "OK", dtype=object)
    passed = np.zeros(shape, dtype=bool)
    return scores, statuses, passed


def run(scores, statuses, passed, names=None):
    names = names or [f"a{i}" for i in range(scores.ndim)]
    return gs.compute(scores, statuses.astype(str), passed, names)


def test_isolated_peak_scores_below_the_plateau():
    s, st, p = grid((11, 11))
    s[2, 2] = 0.20                       # a single needle
    s[6:9, 6:9] = 0.08                   # a plateau ...
    s[7, 7] = 0.10                       # ... with its centre
    p[2, 2] = True
    p[6:9, 6:9] = True                   # P4 passes: the needle and the whole plateau
    r = run(s, st, p)
    needle, centre = r.per_cell[(2, 2)], r.per_cell[(7, 7)]
    # ranking by the raw score would pick the needle ...
    assert needle["score"] > centre["score"]
    # ... the robust score does not
    assert needle["robust_score"] < centre["robust_score"]
    assert needle["robust_score"] == pytest.approx(0.01) and centre["robust_score"] == pytest.approx(0.08)
    assert needle["peak_gap"] == pytest.approx(0.19) and centre["peak_gap"] == pytest.approx(0.02)
    assert needle["peak_ratio"] == pytest.approx(20.0)
    # one region (the plateau), its medoid is the centre; the needle is an isolated pass
    assert len(r.regions) == 1 and r.regions[0]["size"] == 9 and r.regions[0]["medoid"] == (7, 7)
    assert needle["region_status"] == "ISOLATED_PASS" and centre["region_status"] == "IN_REGION"
    assert r.per_cell[(6, 6)]["distance_to_medoid"] == 1
    assert r.regions_reason is None


def test_medoid_rules_l_shape_and_ties():
    s, st, p = grid((5, 5))
    for c in [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2)]:   # an L
        p[c] = True
    r = run(s, st, p)
    # total Chebyshev distances: (1,0)=1+0+1+1+2=5, (2,0)=2+1+0+1+2=6, (2,1)=2+1+1+0+1=5 -> tie
    # tie on cost and robust score -> smaller coordinates
    assert r.regions[0]["medoid"] == (1, 0)
    s[1:4, 0:3] = 0.05  # lift the area around (2,1): its robust score becomes higher than (1,0)'s
    r = run(s, st, p)
    assert r.regions[0]["medoid"] == (2, 1)  # same cost, higher robust score wins


def test_diagonal_cells_form_one_region():
    s, st, p = grid((4, 4))
    p[0, 0] = p[1, 1] = p[2, 2] = True
    r = run(s, st, p)
    assert len(r.regions) == 1 and r.regions[0]["size"] == 3


def test_edges_and_three_axes():
    s, st, p = grid((5, 4))
    r = run(s, st, p)
    assert r.per_cell[(0, 0)]["neighbours"] == 3 and r.per_cell[(0, 1)]["neighbours"] == 5
    assert r.per_cell[(2, 2)]["neighbours"] == 8
    assert r.per_cell[(0, 0)]["edge"] is True and r.per_cell[(0, 1)]["edge"] is False
    s3, st3, p3 = grid((3, 3, 3))
    r3 = run(s3, st3, p3)
    assert r3.per_cell[(1, 1, 1)]["neighbours"] == 26 and r3.per_cell[(0, 0, 0)]["neighbours"] == 7


def test_minus_inf_causes_are_kept_and_counted():
    s, st, p = grid((6, 6), fill=0.02)
    for i, j in itertools.product(range(6), range(6)):
        if i >= j:                       # the idle triangle (fast >= slow)
            s[i, j], st[i, j] = -np.inf, "NO_TRADES"
    s[0, 5], st[0, 5] = -np.inf, "BANKRUPTCY"
    r = run(s, st, p)
    # own status kept apart
    assert r.per_cell[(3, 3)]["score_status"] == "NO_TRADES" and r.per_cell[(0, 5)]["score_status"] == "BANKRUPTCY"
    # neighbour counts agree with a direct count, for every cell
    for (i, j), v in r.per_cell.items():
        want: dict[str, int] = {}
        for di, dj in itertools.product((-1, 0, 1), repeat=2):
            if (di, dj) == (0, 0) or not (0 <= i + di < 6 and 0 <= j + dj < 6):
                continue
            k = str(st[i + di, j + dj])
            want[k] = want.get(k, 0) + 1
        assert v["neighbour_status_counts"] == want, (i, j)
        assert sum(want.values()) == v["neighbours"]
        # never a bare null and never NaN
        for key in ("robust_score", "peak_gap", "peak_ratio", "neighbour_median"):
            if v[key] is None:
                assert v[f"{key}_reason"], (i, j, key)
            else:
                assert math.isfinite(v[key])
    # the reason names the cause
    v = r.per_cell[(1, 4)]  # next to the bankrupt corner: 1 of 9 values is -inf
    assert v["neighbour_status_counts"] == {"OK": 7, "BANKRUPTCY": 1}
    assert v["robust_score"] == pytest.approx(0.02)  # one -inf does not reach the 25% quantile of 9 values
    assert v["neighbour_median"] == pytest.approx(0.02)
    v = r.per_cell[(1, 2)]
    assert v["robust_score"] is None and "NO_TRADES" in v["robust_score_reason"]


def test_no_filter_pass_still_evaluates_everyone():
    s, st, p = grid((4, 5))
    s[1, 1] = 0.3
    r = run(s, st, p)
    assert len(r.per_cell) == 20
    assert r.regions == [] and r.regions_reason == "NO_FILTER_PASS"
    assert {v["region_status"] for v in r.per_cell.values()} == {"NOT_FILTER_PASS"}
    assert all(v["robust_score"] is not None for v in r.per_cell.values())
    s2, st2, p2 = grid((4, 4))
    p2[0, 0] = True
    assert run(s2, st2, p2).regions_reason == "ALL_ISOLATED"


def test_peak_ratio_is_not_computed_around_zero_or_negative():
    s, st, p = grid((3, 3), fill=-0.01)
    s[1, 1] = 0.02
    v = run(s, st, p).per_cell[(1, 1)]
    assert v["peak_ratio"] is None and "<= 0" in v["peak_ratio_reason"] and v["peak_gap"] == pytest.approx(0.03)


def test_axis_sensitivity():
    s, st, p = grid((4, 3))
    for i in range(4):
        s[i, :] = 0.01 * i               # changes along the first axis only
    r = run(s, st, p, ["fast", "slow"])
    assert r.axis_sensitivity["fast"] == pytest.approx(0.01) and r.axis_sensitivity["slow"] == pytest.approx(0.0)
    assert r.per_cell[(1, 1)]["sensitivity_local"]["fast"] == pytest.approx(0.01)


@pytest.mark.parametrize(("m", "status", "flags"), [
    ({"bankrupt": True, "stop_out_deals": 2, "trades": 5, "daily_sharpe": 0.1}, "BANKRUPTCY", ["BANKRUPTCY", "STOP_OUT"]),
    ({"bankrupt": False, "stop_out_deals": 1, "trades": 5, "daily_sharpe": 0.1}, "STOP_OUT", ["STOP_OUT"]),
    ({"bankrupt": False, "stop_out_deals": 0, "trades": 0, "daily_sharpe": None}, "NO_TRADES", ["NO_TRADES"]),
    ({"bankrupt": False, "stop_out_deals": 0, "trades": 9, "daily_sharpe": None}, "NOT_COMPUTABLE", ["NOT_COMPUTABLE"]),
    ({"bankrupt": False, "stop_out_deals": 0, "trades": 9, "daily_sharpe": 0.05}, "OK", ["OK"]),
])
def test_score_status(m, status, flags):
    score, st, fl = gs.score_of(m)
    assert st == status and fl == flags
    assert (score == 0.05) if status == "OK" else score == -math.inf


def test_yearly_consistency():
    dates = ["2022.01.03", "2022.06.01", "2023.01.02", "2023.06.01"]
    eq = [10_010.0, 10_020.0, 10_005.0, 9_990.0]  # 2022: +20, 2023: -30
    y = yearly(dates, eq, 10_000.0)
    assert y["years"] == 2 and y["positive_year_share"] == 0.5
    assert y["per_year"]["2022"]["return"] == pytest.approx(0.002)
    assert y["worst_year_return"] == pytest.approx(-0.003)
    assert yearly(dates[:2], eq[:2], 10_000.0)["reason"].startswith("fewer than 2")
