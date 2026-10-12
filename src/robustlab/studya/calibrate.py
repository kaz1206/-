"""Edge strength as an EXPERIMENTAL CONDITION (SPEC §4.3, s3-C2 revised by §12.4): the scale of each edge component
such that the MEAN of the per-world Sharpe ratios of the reference cell (L = 756, H = 18) equals the target, over
`worlds` (= 40) INDEPENDENT synthetic worlds of the measurement length (~19 years each, tested from the warm-up end to
the path end; spread included, no swap). Each world's Sharpe is computed on that world's own daily returns; worlds
are never concatenated. Recorded for the reference cell and for each of the 60 cells: the 40 per-world values and
their mean, median, sd, min, max, the standard error of the mean (sd / sqrt(40), the measured uncertainty) and the
difference from the target. Seeds: base_seed with world index 0..39 (common to E1 / E2 and to H0), the edge seeds of
the config with the same world index. Saved BEFORE any measurement, hashed into every P result, never overwritten.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from robustlab.core import ids
from robustlab.studya import evaluate as E
from robustlab.studya import prices, t1

CALIBRATION_VERSION = "ca2"
AGGREGATION = "mean of per-world Sharpe ratios"


def sharpe_world(st: E.Setup, p: np.ndarray, cell: tuple[int, int]) -> float:
    """Annualized Sharpe (mean / sd ddof=1 * sqrt(252)) of ONE world's daily returns r_t = dE_t / deposit."""
    a = st.cfg["account"]
    dep = float(a["deposit"])
    w = t1.Window(st.windows["D"].s_start, len(st.days) * st.spd)
    r = t1.run_test(p, w, cell[0], cell[1], lots=float(st.cfg["lots"]), spread=float(a["spread"]),
                    contract=float(a["contract"]), deposit=dep, leverage=float(a["leverage"]),
                    spb=st.pm.steps_per_bar, spd=st.spd, days=st.days, full=False,
                    stop_out_pct=float(a["stop_out_pct"]))
    x = np.diff(np.concatenate([[dep], r.eq_close])) / dep
    sd = float(x.std(ddof=1))
    return float(x.mean() / sd * math.sqrt(252)) if sd > 0 else float("nan")


def summary(values: list[float], target: float | None = None) -> dict[str, Any]:
    v = np.array(values, dtype=float)
    ok = v[np.isfinite(v)]
    out: dict[str, Any] = {"n": len(v), "n_finite": len(ok)}
    if len(ok):
        out.update({"mean": float(ok.mean()), "median": float(np.median(ok)), "sd": float(ok.std(ddof=1)) if len(ok) > 1
                    else None, "min": float(ok.min()), "max": float(ok.max())})
        out["se_mean"] = out["sd"] / math.sqrt(len(ok)) if out["sd"] is not None else None
        if target is not None:
            out["diff_from_target"] = out["mean"] - target
    return out


def _cells_block(st: E.Setup, paths: list[np.ndarray], cells: list[tuple[int, int]]) -> dict[str, Any]:
    per = {f"{L}_{H}": [sharpe_world(st, p, (L, H)) for p in paths] for L, H in cells}
    summ = {k: summary(v) for k, v in per.items()}
    means = {k: s.get("mean", float("-inf")) for k, s in summ.items()}
    best = max(means, key=means.get)
    return {"per_world": per, "summary": summ, "max_cell_by_mean": best, "max_mean": means[best],
            "all_cells_per_world_summary": summary([x for v in per.values() for x in v])}


def calibrate(st: E.Setup, progress=None) -> dict[str, Any]:
    c = st.cfg["calibration"]
    if c.get("aggregation") != "mean_of_per_world_sharpe":
        raise ValueError(f"calibration aggregation {c.get('aggregation')!r}: only mean_of_per_world_sharpe (SPEC §12.4)")
    worlds = int(c["worlds"])
    start = float(st.cfg["account"]["start_price"])
    bases = [prices.base_steps(st.n_bars, st.pm, int(c["base_seed"]), i) for i in range(worlds)]
    ref = (int(st.cfg["reference_cell"]["InpLookback"]), int(st.cfg["reference_cell"]["InpHoldBars"]))
    cells = [(L, H) for L in st.lookbacks for H in st.holds]
    tol = float(c["tolerance"])
    years = (len(st.days) - int(st.cfg["calendar"]["warmup_weekdays"])) / 261
    out: dict[str, Any] = {"version": CALIBRATION_VERSION, "config_hash": st.cfg_hash, "worlds": worlds,
                           "years_per_world": round(years, 2), "reference_cell": list(ref),
                           "base_seed": int(c["base_seed"]), "world_indices": [0, worlds - 1],
                           "aggregation": AGGREGATION, "tolerance": tol,
                           "definition": "per world: annualized daily Sharpe (mean/sd ddof=1 * sqrt(252)) of "
                                         "r_t = dE_t / 10,000 from the warm-up end to the path end (spread included, "
                                         "no swap / commission, stop-out as in the model); target = mean over the "
                                         "worlds (no concatenation); bisection on the scale until |mean - target| <= "
                                         "tolerance / 4 (accepted up to tolerance)", "edges": {}}
    p0 = [prices.path(b, start) for b in bases]
    out["h0"] = _cells_block(st, p0, cells)
    out["h0"]["reference"] = summary(out["h0"]["per_world"][f"{ref[0]}_{ref[1]}"], 0.0)
    if progress:
        r0 = out["h0"]["reference"]
        progress(f"H0 reference: mean {r0['mean']:.4f} sd {r0['sd']:.4f} se {r0['se_mean']:.4f}")
    for name, e in st.cfg["edges"].items():
        units = [prices.edge_unit(e["kind"], st.n_bars, st.pm.steps_per_bar, int(c["seeds"][name]), i,
                                  persistence_bars=float(e.get("persistence_bars", 0)),
                                  half_life_bars=float(e.get("half_life_bars", 0))) for i in range(worlds)]

        def paths_at(scale: float, units: list[np.ndarray] = units) -> list[np.ndarray]:
            return [prices.path(b + scale * u, start) for b, u in zip(bases, units, strict=True)]

        def f(scale: float) -> float:
            return float(np.mean([sharpe_world(st, p, ref) for p in paths_at(scale)]))

        res = {}
        for target in c["targets"]:
            target = float(target)
            lo, hi = 0.0, 1e-5
            evals = []
            while (v := f(hi)) < target:
                evals.append([hi, v])
                lo, hi = hi, hi * 2
                if hi > 1:
                    raise RuntimeError(f"{name}: no scale reaches a mean Sharpe of {target}")
            evals.append([hi, v])
            got, mid = None, hi
            for _ in range(60):
                mid = (lo + hi) / 2
                got = f(mid)
                evals.append([mid, got])
                if abs(got - target) <= tol / 4:
                    break
                lo, hi = (mid, hi) if got < target else (lo, mid)
            if got is None or abs(got - target) > tol:
                raise RuntimeError(f"{name} {target}: calibration missed the tolerance ({got})")
            ps = paths_at(mid)
            block = _cells_block(st, ps, cells)
            block["reference"] = summary(block["per_world"][f"{ref[0]}_{ref[1]}"], target)
            res[str(target)] = {"scale": mid, "seed": int(c["seeds"][name]), "bisection": evals, **block,
                                "price_min": float(min(p.min() for p in ps)),
                                "price_max": float(max(p.max() for p in ps))}
            if progress:
                r = block["reference"]
                progress(f"{name} target {target}: scale {mid:.6g}, reference mean {r['mean']:.4f} (median "
                         f"{r['median']:.4f}, sd {r['sd']:.4f}, se {r['se_mean']:.4f}, min {r['min']:.3f}, max "
                         f"{r['max']:.3f}), best cell {block['max_cell_by_mean']} {block['max_mean']:.4f}, price "
                         f"{res[str(target)]['price_min']:.3f}-{res[str(target)]['price_max']:.3f}")
        out["edges"][name] = res
    return out


def save(out: dict[str, Any], path: Path) -> str:
    text = json.dumps(out, indent=1, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"{path} exists: the calibration is fixed before the measurements (SPEC §4.3)")
    path.write_text(text, encoding="utf-8")
    return ids.sha256_hex(text)
