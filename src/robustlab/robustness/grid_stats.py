"""Neighbourhood statistics and parameter regions on a full grid (ARCHITECTURE §9, P5_PLAN §3, C56-C59).

Pure functions over numpy arrays shaped like the grid (one axis per optimized input, values in grid order).
- Neighbourhood N(c): Chebyshev distance 1, cells outside the grid are NOT counted (mode='constant',
  cval=NaN; SciPy's default 'reflect' would duplicate edge values) [S-SCIPY-NDIMAGE].
- Quantiles use method='inverted_cdf' (no interpolation): 'linear' returns NaN with -inf [S-NUMPY-PCT].
- Scores that cannot be used are -inf inside the computation, but their cause is kept (score_status).
- Regions: P4-pass cells connected with diagonals (generate_binary_structure(d, d)) (C57).
"""

from __future__ import annotations

import itertools
import math
import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

GRID_STATS_VERSION = "g1"
QUANTILE_METHOD = "inverted_cdf"
ROBUST_QUANTILE = 25.0
STATUS_ORDER = ("BANKRUPTCY", "STOP_OUT", "NO_TRADES", "NOT_COMPUTABLE", "OK")  # priority, most severe first


def score_of(m: dict[str, Any]) -> tuple[float, str, list[str]]:
    """(score, status, all flags) for one candidate's metrics (N1, N8). Unusable scores are -inf."""
    flags = []
    if m.get("bankrupt") is True:
        flags.append("BANKRUPTCY")
    so = m.get("stop_out_deals")
    if isinstance(so, (int, float)) and not isinstance(so, bool) and so > 0:
        flags.append("STOP_OUT")
    tr = m.get("trades")
    if tr == 0:
        flags.append("NO_TRADES")
    sr = m.get("daily_sharpe")
    sr_ok = isinstance(sr, (int, float)) and not isinstance(sr, bool) and math.isfinite(sr)
    if not sr_ok and "NO_TRADES" not in flags:
        flags.append("NOT_COMPUTABLE")
    if not flags:
        return float(sr), "OK", ["OK"]
    status = next(s for s in STATUS_ORDER if s in flags)
    return float("-inf"), status, flags


def _footprint(d: int, with_center: bool) -> np.ndarray:
    fp = np.ones((3,) * d, dtype=bool)
    if not with_center:
        fp[(1,) * d] = False
    return fp


def _filter(a: np.ndarray, fn, *, with_center: bool, cval: float = np.nan) -> np.ndarray:
    return ndimage.generic_filter(a.astype(float), fn, footprint=_footprint(a.ndim, with_center), mode="constant",
                                  cval=cval)


def _quantile(v: np.ndarray) -> float:
    v = v[~np.isnan(v)]
    return float(np.percentile(v, ROBUST_QUANTILE, method=QUANTILE_METHOD)) if v.size else np.nan


def _median(v: np.ndarray) -> float:
    v = v[~np.isnan(v)]
    return float(np.percentile(v, 50.0, method=QUANTILE_METHOD)) if v.size else np.nan


def _count(v: np.ndarray) -> float:
    return float(np.count_nonzero(~np.isnan(v)))


def _clean(x: float, reason: str) -> tuple[float | None, str | None]:
    """-inf/NaN never get stored as numbers: None plus a reason."""
    if x is None or not math.isfinite(x):
        return None, reason
    return float(x), None


@dataclass
class GridResult:
    per_cell: dict[tuple[int, ...], dict[str, Any]]
    regions: list[dict[str, Any]]
    regions_reason: str | None
    axis_sensitivity: dict[str, float | None]


def compute(scores: np.ndarray, statuses: np.ndarray, passed: np.ndarray, axis_names: list[str]) -> GridResult:
    """scores: float grid (-inf for unusable, NaN for cells without a candidate), statuses: str grid,
    passed: bool grid (P4 decision)."""
    d = scores.ndim
    full = 3 ** d - 1
    robust = _filter(scores, _quantile, with_center=True)
    nb_median = _filter(scores, _median, with_center=False)
    nb_count = _filter(np.where(np.isnan(scores), np.nan, 0.0), _count, with_center=False)
    present = ~np.isnan(scores)
    pass_f = np.where(present, passed.astype(float), np.nan)
    nb_pass = _filter(pass_f, lambda v: float(np.nanmean(v)) if np.any(~np.isnan(v)) else np.nan, with_center=False)
    breakdown = {s: _filter((statuses == s).astype(float), np.sum, with_center=False, cval=0.0) for s in STATUS_ORDER}

    # per-axis sensitivity: |difference| of finite neighbours along the axis
    axis_sens: dict[str, float | None] = {}
    local_sens: dict[str, np.ndarray] = {}
    for ax, name in enumerate(axis_names):
        with np.errstate(invalid="ignore"):
            diff = np.abs(np.diff(scores, axis=ax))
        finite = diff[np.isfinite(diff)]
        axis_sens[name] = float(np.median(finite)) if finite.size else None
        pad = [(0, 0)] * d
        pad[ax] = (1, 0)
        before = np.pad(diff, pad, constant_values=np.nan)
        pad[ax] = (0, 1)
        after = np.pad(diff, pad, constant_values=np.nan)
        both = np.stack([before, after])
        both[~np.isfinite(both)] = np.nan
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices are handled by the np.where
            local_sens[name] = np.where(np.all(np.isnan(both), axis=0), np.nan, np.nanmax(both, axis=0))

    # regions: P4-pass cells, diagonal connectivity (C57)
    labels, n = ndimage.label(passed & present, structure=ndimage.generate_binary_structure(d, d))
    regions: list[dict[str, Any]] = []
    region_of: dict[tuple[int, ...], dict[str, Any]] = {}
    for lab in range(1, n + 1):
        members = [tuple(int(i) for i in idx) for idx in np.argwhere(labels == lab)]
        if len(members) == 1:
            region_of[members[0]] = {"status": "ISOLATED_PASS"}
            continue

        def cost(c: tuple[int, ...], members=members) -> int:
            return sum(max(abs(a - b) for a, b in zip(c, o, strict=True)) for o in members)

        # medoid: least total Chebyshev distance, then higher Robust Score, then smaller coordinates
        medoid = min(members, key=lambda c: (cost(c), -robust[c], c))
        reg = {"index": len(regions), "members": sorted(members), "medoid": medoid, "size": len(members)}
        regions.append(reg)
        for c in members:
            region_of[c] = {"status": "IN_REGION", "region": reg["index"], "size": len(members),
                            "distance_to_medoid": max(abs(a - b) for a, b in zip(c, medoid, strict=True))}
    if not np.any(passed & present):
        regions_reason = "NO_FILTER_PASS"
    elif not regions:
        regions_reason = "ALL_ISOLATED"
    else:
        regions_reason = None

    per_cell: dict[tuple[int, ...], dict[str, Any]] = {}
    for idx in itertools.product(*(range(s) for s in scores.shape)):
        if not present[idx]:
            continue
        s = scores[idx]
        nb = {k: int(v[idx]) for k, v in breakdown.items() if v[idx] > 0}
        neg = {k: v for k, v in nb.items() if k != "OK"}
        nb_txt = ", ".join(f"{k} {v}" for k, v in neg.items())
        r, r_reason = _clean(robust[idx], f"neighbourhood includes -inf scores ({nb_txt}; self {statuses[idx]})")
        med, med_reason = _clean(nb_median[idx], f"neighbour median is -inf ({nb_txt})")
        if not math.isfinite(s):
            gap, gap_reason = None, f"own score is -inf ({statuses[idx]})"
        elif med is None:
            gap, gap_reason = None, med_reason
        else:
            gap, gap_reason = float(s - med), None
        if gap is None:
            ratio, ratio_reason = None, gap_reason
        elif med <= 0:
            ratio, ratio_reason = None, "neighbour median <= 0: a ratio has no meaning (C56)"
        else:
            ratio, ratio_reason = float(s / med), None
        cnt = int(nb_count[idx])
        reg = region_of.get(idx, {"status": "NOT_FILTER_PASS"}) if passed[idx] else {"status": "NOT_FILTER_PASS"}
        per_cell[idx] = {
            "score": None if not math.isfinite(s) else float(s), "score_status": str(statuses[idx]),
            "robust_score": r, "robust_score_reason": r_reason,
            "neighbour_median": med, "neighbour_median_reason": med_reason,
            "peak_gap": gap, "peak_gap_reason": gap_reason, "peak_ratio": ratio, "peak_ratio_reason": ratio_reason,
            "neighbours": cnt, "neighbours_full": full, "edge": cnt < full / 2,
            "neighbour_status_counts": nb,
            "neighbour_pass_rate": None if math.isnan(nb_pass[idx]) else float(nb_pass[idx]),
            "sensitivity_local": {k: (None if math.isnan(v[idx]) else float(v[idx])) for k, v in local_sens.items()},
            "region_status": reg["status"], "region_index": reg.get("region"), "region_size": reg.get("size", 0),
            "distance_to_medoid": reg.get("distance_to_medoid"),
        }
    return GridResult(per_cell, regions, regions_reason, axis_sens)
