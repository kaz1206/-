"""P7 gates, region representatives and the integrated ranking (P7_PLAN §3, R1-R8, C68-C70).

Pure functions over the grid (one axis per optimized input). No database access here.

- Neighbourhood: Chebyshev distance 1, cells outside the grid are not counted (as in P5).
- R3 (独自拡張, C69): NO_TRADES neighbours are not counted; BANKRUPTCY / STOP_OUT / NOT_COMPUTABLE neighbours
  are counted as -inf, and any BANKRUPTCY / STOP_OUT neighbour fails the candidate. At least
  ceil((3^d - 1) / 2) counted neighbours are required, otherwise NEIGHBORHOOD_INSUFFICIENT.
- R4 (C70): DSR is never a gate, a reason code or a threshold. It enters only the integrated ranking, as ranks.
- R5 (C68): integrated ranking = Pareto layers -> mean of the three per-objective ranks -> fixed tie-break
  (Robust Score rank, max DD rank, DSR rank, candidate_id). Only ranks are used, so the scales do not matter.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import rankdata

SELECTOR = "region_pareto_v1"
SELECTION_VERSION = "s1"
QUANTILE_METHOD = "inverted_cdf"  # as P5 (C58)
NOT_COUNTED = frozenset({"NO_TRADES"})
RUIN = frozenset({"BANKRUPTCY", "STOP_OUT"})
ROUND_SCORE, ROUND_DSR, ROUND_DD = 9, 9, 2
PASS, FAIL, NOT_EVALUATED = "PASS", "FAIL", "NOT_EVALUATED"

# code -> (category, description, severity)
REASON_CODES: dict[str, tuple[str, str, str]] = {
    "ISOLATED_PEAK": ("robustness", "isolated P4 pass, or own score far above the neighbour median (ratio > max, "
                                    "or neighbour median <= 0)", "FAIL"),
    "NEIGHBORHOOD_INSUFFICIENT": ("robustness", "fewer counted neighbours than ceil((3^d-1)/2) after leaving out "
                                                "no-trade neighbours (R3, C69)", "FAIL"),
    "NEIGHBORHOOD_UNSTABLE": ("robustness", "a bankrupt / stop-out neighbour, or the 25% quantile of the "
                                            "neighbourhood (Robust Score) <= 0", "FAIL"),
    "REDUNDANT_IN_REGION": ("selection", "another candidate represents the same region", "NOT_SELECTED"),
    "DOMINATED": ("selection", "a selected candidate is at least as good on all three ranking objectives and "
                               "better on one", "NOT_SELECTED"),
    "NOT_SELECTED_BUDGET": ("selection", "outside the selection budget", "NOT_SELECTED"),
    "LOW_DSR": ("statistical_sufficiency", "conservative DSR below the warning level (warning only, C62)", "WARN"),
    "SHARPE_CI_INCLUDES_ZERO": ("statistical_sufficiency", "bootstrap 90% Sharpe interval contains 0", "WARN"),
    "EDGE": ("robustness", "fewer than half of the full neighbourhood inside the grid (P5)", "WARN"),
    "NO_TRADE_NEIGHBOURS": ("robustness", "no-trade neighbours were left out of the neighbourhood (R3)", "WARN"),
    "SUBPERIOD_INCONSISTENT": ("robustness", "share of profitable years < 0.5 or a losing year", "WARN"),
    "RANK_INPUT_MISSING": ("selection", "a ranking objective is missing and counted as the worst value", "WARN"),
    "SELECTION_REVISED": ("selection", "the round was selected before with other inputs or settings", "WARN"),
}
DSR_CODES = frozenset({"LOW_DSR"})  # must never appear in reason codes (R4)


def neighbour_floor(d: int) -> int:
    return math.ceil((3 ** d - 1) / 2)


def _q(values: list[float], p: float) -> float:
    return float(np.percentile(np.array(values, dtype=float), p, method=QUANTILE_METHOD))


@dataclass
class Neighbourhood:
    counted: int
    no_trade: int
    ruin: int
    not_computable: int
    robust_score: float | None  # 25% quantile of self + counted neighbours (may be -inf -> None with reason)
    neighbour_median: float | None


def neighbourhood(idx: tuple[int, ...], scores: np.ndarray, statuses: np.ndarray) -> Neighbourhood:
    """R3 counting around one cell. scores: daily Sharpe for OK cells (NaN where there is no candidate)."""
    vals: list[float] = []
    no_trade = ruin = not_comp = 0
    for off in itertools.product((-1, 0, 1), repeat=scores.ndim):
        if not any(off):
            continue
        n = tuple(i + o for i, o in zip(idx, off, strict=True))
        if any(k < 0 or k >= s for k, s in zip(n, scores.shape, strict=True)):
            continue
        st = str(statuses[n])
        if st == "" or (st == "OK" and math.isnan(scores[n])):
            continue  # no candidate in that cell: same as outside the grid
        if st in NOT_COUNTED:
            no_trade += 1
            continue
        if st == "OK":
            vals.append(float(scores[n]))
            continue
        ruin += st in RUIN
        not_comp += st not in RUIN
        vals.append(-math.inf)
    own = float(scores[idx])
    rob = _q([own, *vals], 25.0)
    med = _q(vals, 50.0) if vals else None
    # the median may be -inf (more than half the counted neighbours ruined): the peak gate treats it as <= 0
    return Neighbourhood(len(vals), no_trade, ruin, not_comp, rob if math.isfinite(rob) else None, med)


@dataclass
class Gate:
    gate: str
    code: str
    status: str
    value: Any
    threshold: Any
    note: str = ""


def gates(own: float, region_status: str, nb: Neighbourhood, d: int, peak_ratio_max: float,
          robust_min_exclusive: float) -> list[Gate]:
    """All gates for one P4-passing candidate; never stops at the first failure."""
    floor = neighbour_floor(d)
    out = [Gate("isolated_pass", "ISOLATED_PEAK", FAIL if region_status == "ISOLATED_PASS" else PASS,
                region_status, "not ISOLATED_PASS"),
           Gate("neighbour_count", "NEIGHBORHOOD_INSUFFICIENT", FAIL if nb.counted < floor else PASS, nb.counted,
                floor, f"no-trade neighbours left out: {nb.no_trade}"),
           Gate("neighbour_ruin", "NEIGHBORHOOD_UNSTABLE", FAIL if nb.ruin else PASS, nb.ruin, 0,
                "bankrupt / stop-out neighbours")]
    if nb.robust_score is None:
        out.append(Gate("robust_score", "NEIGHBORHOOD_UNSTABLE", FAIL, None, f"> {robust_min_exclusive}",
                        "25% quantile is -inf (bankrupt / not computable neighbours)"))
    else:
        out.append(Gate("robust_score", "NEIGHBORHOOD_UNSTABLE",
                        FAIL if nb.robust_score <= robust_min_exclusive else PASS, nb.robust_score,
                        f"> {robust_min_exclusive}"))
    med = nb.neighbour_median
    if med is None and nb.counted == 0:
        out.append(Gate("peak_ratio", "ISOLATED_PEAK", NOT_EVALUATED, None, f"<= {peak_ratio_max}",
                        "no counted neighbours (see neighbour_count)"))
    elif med is None or not math.isfinite(med) or med <= 0:
        out.append(Gate("peak_ratio", "ISOLATED_PEAK", FAIL, None, f"<= {peak_ratio_max}",
                        f"neighbour median {med} <= 0 while the own score is {own:.6g}: a peak (C56)"))
    else:
        ratio = own / med
        out.append(Gate("peak_ratio", "ISOLATED_PEAK", FAIL if ratio > peak_ratio_max else PASS, ratio,
                        f"<= {peak_ratio_max}"))
    return out


def reasons_of(gs: list[Gate]) -> list[str]:
    seen: list[str] = []
    for g in gs:
        if g.status == FAIL and g.code not in seen:
            seen.append(g.code)
    return seen


# ----- integrated ranking (R4, R5) -----

@dataclass(frozen=True)
class RankInput:
    candidate_id: str
    robust_score: float | None
    dsr: float | None
    max_dd_p95: float | None


@dataclass
class Ranked:
    candidate_id: str
    layer: int
    mean_rank: float
    ranks: dict[str, float]
    missing: list[str]
    objectives: tuple[float, float, float] = field(repr=False, default=(0.0, 0.0, 0.0))


def _norm(x: float | None, digits: int, worst: float) -> tuple[float, bool]:
    if x is None or not math.isfinite(x):
        return worst, True
    return round(float(x), digits), False


def dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(x >= y for x, y in zip(a, b, strict=True)) and any(x > y for x, y in zip(a, b, strict=True))


def rank(items: list[RankInput]) -> list[Ranked]:
    """Integrated ranking, deterministic and independent of the input order (P7_PLAN §3.5-2)."""
    items = sorted(items, key=lambda r: r.candidate_id)
    rows = []
    for it in items:
        r, mr = _norm(it.robust_score, ROUND_SCORE, -math.inf)
        d, md = _norm(it.dsr, ROUND_DSR, -math.inf)
        dd, mdd = _norm(it.max_dd_p95, ROUND_DD, math.inf)
        missing = [n for n, m in (("robust_score", mr), ("dsr", md), ("max_dd_p95", mdd)) if m]
        rows.append((it.candidate_id, (r, d, -dd), missing))  # all three "larger is better"
    if not rows:
        return []
    obj = np.array([o for _, o, _ in rows], dtype=float)
    # rank 1 = best; ties get the average rank
    rk = {name: rankdata(-obj[:, k], method="average") for k, name in enumerate(("robust_score", "dsr", "max_dd_p95"))}
    layer = {}
    left = list(range(len(rows)))
    n_layer = 0
    while left:
        n_layer += 1
        front = [i for i in left if not any(dominates(tuple(obj[j]), tuple(obj[i])) for j in left if j != i)]
        for i in front:
            layer[i] = n_layer
        left = [i for i in left if i not in front]
    out = []
    for i, (cid, o, missing) in enumerate(rows):
        ranks = {k: float(v[i]) for k, v in rk.items()}
        out.append(Ranked(cid, layer[i], (ranks["robust_score"] + ranks["dsr"] + ranks["max_dd_p95"]) / 3, ranks,
                          missing, o))
    out.sort(key=lambda x: (x.layer, x.mean_rank, x.ranks["robust_score"], x.ranks["max_dd_p95"], x.ranks["dsr"],
                            x.candidate_id))
    return out


def chebyshev(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def representative(members: list[str], medoid_index: tuple[int, ...], index_of: dict[str, tuple[int, ...]],
                   rank_input: dict[str, RankInput]) -> str | None:
    """Gate-passing member closest to the medoid; ties -> integrated rank among the tied, then candidate_id."""
    if not members:
        return None
    best = min(chebyshev(index_of[c], medoid_index) for c in members)
    tied = [c for c in members if chebyshev(index_of[c], medoid_index) == best]
    if len(tied) == 1:
        return tied[0]
    return rank([rank_input[c] for c in tied])[0].candidate_id


@dataclass
class Selection:
    selected: list[str]  # in integrated-rank order
    ranking: list[Ranked]
    not_selected: dict[str, str]  # representative -> DOMINATED / NOT_SELECTED_BUDGET


def select(reps: list[RankInput], budget: int) -> Selection:
    ranking = rank(reps)
    chosen = ranking[:budget]
    chosen_obj = [r.objectives for r in chosen]
    rest = {}
    for r in ranking[budget:]:
        rest[r.candidate_id] = ("DOMINATED" if any(dominates(o, r.objectives) for o in chosen_obj)
                                else "NOT_SELECTED_BUDGET")
    return Selection([r.candidate_id for r in chosen], ranking, rest)


@dataclass
class Decided:
    per: dict[str, dict[str, Any]]  # candidate -> nb, gates, reasons, rank_input
    passed: list[str]
    representatives: list[str]
    redundant: dict[str, str]  # gate-passing non-representative -> its region's representative
    selection: Selection


def decide(scores: np.ndarray, statuses: np.ndarray, eligible: list[str], index_of: dict[str, tuple[int, ...]],
           region_status: dict[str, str], regions: list[tuple[list[str], str]],
           extra: dict[str, tuple[float | None, float | None]], budget: int, peak_ratio_max: float,
           robust_min_exclusive: float) -> Decided:
    """The whole region_pareto_v1 decision on one grid (used by run.py and by the benchmark).
    regions: (members, medoid) from P5; extra: candidate -> (conservative DSR, resampled max DD p95)."""
    d = scores.ndim
    per: dict[str, dict[str, Any]] = {}
    for cd in sorted(eligible):
        idx = index_of[cd]
        nb = neighbourhood(idx, scores, statuses)
        gs = gates(float(scores[idx]), region_status[cd], nb, d, peak_ratio_max, robust_min_exclusive)
        dsr, dd = extra.get(cd, (None, None))
        per[cd] = {"nb": nb, "gates": gs, "reasons": reasons_of(gs), "rank_input": RankInput(cd, nb.robust_score,
                                                                                               dsr, dd)}
    passed = [cd for cd in sorted(eligible) if not per[cd]["reasons"]]
    in_region = {cd for members, _ in regions for cd in members}
    orphans = [cd for cd in passed if cd not in in_region]
    if orphans:
        raise ValueError(f"gate passes outside every region: {orphans[:3]}")
    reps: list[str] = []
    redundant: dict[str, str] = {}
    pset = set(passed)
    for members, medoid in sorted(regions, key=lambda r: r[1]):
        ms = sorted(cd for cd in members if cd in pset)
        rep = representative(ms, index_of[medoid], index_of, {cd: per[cd]["rank_input"] for cd in ms})
        if rep is None:
            continue
        reps.append(rep)
        redundant.update({cd: rep for cd in ms if cd != rep})
    return Decided(per, passed, reps, redundant, select([per[cd]["rank_input"] for cd in reps], budget))
