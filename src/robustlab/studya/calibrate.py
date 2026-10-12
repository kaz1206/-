"""Edge strength as an EXPERIMENTAL CONDITION (SPEC §4.3, change s3-C2): the scale of each edge component such that
the reference cell (L = 756, H = 18) has the target annualized daily Sharpe on the pooled daily returns of `worlds`
independent synthetic worlds of the measurement length (10 x ~20 years = ~200 years; spread included, no swap), and
the true Sharpe of all 60 cells at that scale. One 200-year path (s2) is not usable with the multiplicative price:
a strong regime edge compounds over two centuries and drives the price to 0. Saved BEFORE any measurement and hashed
into every P result.
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

CALIBRATION_VERSION = "ca1"


def sharpe_pooled(st: E.Setup, paths: list[np.ndarray], cell: tuple[int, int]) -> float:
    """Annualized Sharpe of the pooled daily returns of one cell, each world tested from the warm-up end to the end."""
    a = st.cfg["account"]
    dep = float(a["deposit"])
    w = t1.Window(st.windows["D"].s_start, len(st.days) * st.spd)
    rets = []
    for p in paths:
        r = t1.run_test(p, w, cell[0], cell[1], lots=float(st.cfg["lots"]), spread=float(a["spread"]),
                        contract=float(a["contract"]), deposit=dep, leverage=float(a["leverage"]),
                        spb=st.pm.steps_per_bar, spd=st.spd, days=st.days, full=False)
        rets.append(np.diff(np.concatenate([[dep], r.eq_close])) / dep)
    x = np.concatenate(rets)
    return float(x.mean() / x.std(ddof=1) * math.sqrt(252))


def calibrate(st: E.Setup, progress=None) -> dict[str, Any]:
    c = st.cfg["calibration"]
    worlds = int(c["worlds"])
    start = float(st.cfg["account"]["start_price"])
    bases = [prices.base_steps(st.n_bars, st.pm, int(c["base_seed"]), i) for i in range(worlds)]
    ref = (int(st.cfg["reference_cell"]["InpLookback"]), int(st.cfg["reference_cell"]["InpHoldBars"]))
    cells = [(L, H) for L in st.lookbacks for H in st.holds]
    tol = float(c["tolerance"])
    years = worlds * (len(st.days) - int(st.cfg["calendar"]["warmup_weekdays"])) / 261
    out: dict[str, Any] = {"version": CALIBRATION_VERSION, "config_hash": st.cfg_hash, "worlds": worlds,
                           "years_pooled": round(years, 1), "reference_cell": list(ref),
                           "base_seed": int(c["base_seed"]),
                           "definition": "annualized daily Sharpe (mean/sd ddof=1 * sqrt(252)) of r_t = dE_t / 10,000, "
                                         "pooled over the worlds, each tested from the warm-up end to the path end; "
                                         "spread included, no swap / commission", "edges": {}}
    p0 = [prices.path(b, start) for b in bases]
    out["h0_cells"] = {f"{L}_{H}": sharpe_pooled(st, p0, (L, H)) for L, H in cells}
    for name, e in st.cfg["edges"].items():
        units = [prices.edge_unit(e["kind"], st.n_bars, st.pm.steps_per_bar, int(c["seeds"][name]), i,
                                  persistence_bars=float(e.get("persistence_bars", 0)),
                                  half_life_bars=float(e.get("half_life_bars", 0))) for i in range(worlds)]

        def paths_at(scale: float, units: list[np.ndarray] = units) -> list[np.ndarray]:
            return [prices.path(b + scale * u, start) for b, u in zip(bases, units, strict=True)]

        def f(scale: float) -> float:
            return sharpe_pooled(st, paths_at(scale), ref)

        res = {}
        for target in c["targets"]:
            lo, hi = 0.0, 1e-5
            while f(hi) < target:
                lo, hi = hi, hi * 2
                if hi > 1:
                    raise RuntimeError(f"{name}: no scale reaches Sharpe {target}")
            got, mid = None, hi
            for _ in range(60):
                mid = (lo + hi) / 2
                got = f(mid)
                if abs(got - target) <= tol / 4:
                    break
                lo, hi = (mid, hi) if got < target else (lo, mid)
            if got is None or abs(got - target) > tol:
                raise RuntimeError(f"{name} {target}: calibration missed the tolerance ({got})")
            ps = paths_at(mid)
            cells_sr = {f"{L}_{H}": sharpe_pooled(st, ps, (L, H)) for L, H in cells}
            best = max(cells_sr, key=cells_sr.get)
            res[str(target)] = {"scale": mid, "reference_sharpe": got, "cells": cells_sr, "max_cell": best,
                                "max_sharpe": cells_sr[best], "seed": int(c["seeds"][name]),
                                "price_min": float(min(p.min() for p in ps)), "price_max": float(max(p.max() for p in ps))}
            if progress:
                progress(f"{name} target {target}: scale {mid:.6g}, reference Sharpe {got:.4f}, best {best} "
                         f"{cells_sr[best]:.4f}, price range {res[str(target)]['price_min']:.3f}-"
                         f"{res[str(target)]['price_max']:.3f}")
        out["edges"][name] = res
    return out


def save(out: dict[str, Any], path: Path) -> str:
    text = json.dumps(out, indent=1, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"{path} exists: the calibration is fixed before the measurements (SPEC §4.3)")
    path.write_text(text, encoding="utf-8")
    return ids.sha256_hex(text)
