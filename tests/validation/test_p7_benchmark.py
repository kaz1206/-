"""P7 selector benchmark on synthetic grids with criteria fixed BEFORE implementation (P7_PLAN §6.1, R8).

30 x 30 grids of smooth spatially correlated noise (gaussian_filter sigma 2, sd 0.02), 500 grids per scenario,
seed 20261011. The production code is used: robustness.grid_stats.compute (P5 regions, medoids) and
selection.core.decide (P7 gates, representatives, integrated ranking, budget 5).
Stand-ins (no return series here): P4 pass = score > 0.05; DSR = the score itself (only its rank is used);
resampled max DD p95 = uniform random.

Pre-registered criteria:
- A (real 6x6 plateau +0.08, 3 isolated spikes 0.15): a plateau cell selected, 95% lower limit >= 90%;
  a spike selected, 95% upper limit <= 5%
- B (no plateau, 3 spikes): a spike selected, 95% upper limit <= 5%
- KEY METRIC, recorded without a criterion (R8): with NO edge (B and C), the share of grids where anything is
  selected and the number selected. Low values are the job of P8 / P10, not of P7.
Set RLAB_P7_RESULTS to a directory to write the numbers as JSON (docs/research/p7_validation/RESULTS.md).
"""

from __future__ import annotations

import json
import multiprocessing
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter
from scipy.stats import beta

from robustlab.robustness import grid_stats
from robustlab.selection import core

SEED = 20261011
S, REPS, BUDGET, P4_THRESHOLD = 30, 500, 5, 0.05

pytestmark = pytest.mark.slow


def cp(k: int, n: int) -> tuple[float, float]:
    lo = float(beta.ppf(0.025, k, n - k + 1)) if k > 0 else 0.0
    hi = float(beta.ppf(0.975, k + 1, n - k)) if k < n else 1.0
    return lo, hi


def make(rng: np.random.Generator, plateau: bool, spikes: int, noise_sd: float = 0.02):
    f = gaussian_filter(rng.standard_normal((S, S)), 2.0)
    f = f / f.std() * noise_sd
    plat = np.zeros((S, S), bool)
    if plateau:
        i, j = rng.integers(2, S - 8, 2)
        plat[i:i + 6, j:j + 6] = True
        f[plat] += 0.08
    spk = np.zeros((S, S), bool)
    while spk.sum() < spikes:
        i, j = rng.integers(1, S - 1, 2)
        if not plat[max(0, i - 2):i + 3, max(0, j - 2):j + 3].any():
            spk[i, j] = True
    f[spk] = 0.15
    return f, plat, spk


def key(idx: tuple[int, ...]) -> str:
    return f"cd_{idx[0]:02d}_{idx[1]:02d}"


def run_grid(f: np.ndarray, rng: np.random.Generator) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(region_pareto_v1 selection, hard_gate_v1 selection) on one synthetic grid."""
    statuses = np.full(f.shape, "OK", dtype=object)
    passed = f > P4_THRESHOLD
    res = grid_stats.compute(f, statuses.astype(str), passed, ["a", "b"])
    cells = [tuple(int(i) for i in c) for c in np.argwhere(passed)]
    index_of = {key(c): c for c in cells}
    regions = [([key(c) for c in r["members"]], key(r["medoid"])) for r in res.regions]
    for members, med in regions:
        index_of.update({m: tuple(int(x) for x in m[3:].split("_")) for m in members})
        index_of[med] = tuple(int(x) for x in med[3:].split("_"))
    dd = rng.uniform(size=f.shape)
    extra = {key(c): (float(f[c]), float(dd[c])) for c in cells}
    dec = core.decide(f, statuses, [key(c) for c in cells], index_of,
                      {key(c): res.per_cell[c]["region_status"] for c in cells}, regions, extra, BUDGET, 1.5, 0.0)
    rp = [index_of[c] for c in dec.selection.selected]
    # hard_gate_v1 (benchmark baseline only, never exposed in the CLI): P4 passes by P5 Robust Score, top K
    hg = sorted((c for c in cells if res.per_cell[c]["robust_score"] is not None),
                key=lambda c: (-res.per_cell[c]["robust_score"], c))[:BUDGET]
    return rp, hg


def scenario(plateau: bool, spikes: int) -> dict:
    rng = np.random.default_rng(SEED)
    c = Counter()
    n_selected = []
    for _ in range(REPS):
        f, plat, spk = make(rng, plateau, spikes)
        rp, hg = run_grid(f, rng)
        c["rp_plateau"] += any(plat[s] for s in rp)
        c["rp_spike"] += any(spk[s] for s in rp)
        c["rp_any"] += bool(rp)
        c["naive_spike"] += bool(spk[np.unravel_index(int(np.argmax(f)), f.shape)])
        c["hg_plateau"] += any(plat[s] for s in hg)
        c["hg_spike"] += any(spk[s] for s in hg)
        n_selected.append(len(rp))
    return {"counts": dict(c), "n_selected": n_selected}


def _pool(args):
    with ProcessPoolExecutor(max_workers=max(1, min(3, os.cpu_count() or 1)),
                             mp_context=multiprocessing.get_context("spawn")) as ex:
        return list(ex.map(scenario, *zip(*args, strict=True)))


def _summary(name: str, r: dict) -> dict:
    c, n = r["counts"], r["n_selected"]
    out = {"scenario": name}
    for k in ("rp_plateau", "rp_spike", "rp_any", "naive_spike", "hg_plateau", "hg_spike"):
        out[k] = {"rate": c.get(k, 0) / REPS, "ci": cp(c.get(k, 0), REPS)}
    out["n_selected_mean"] = float(np.mean(n))
    out["n_selected_distribution"] = {str(k): v for k, v in sorted(Counter(n).items())}
    return out


def test_selector_benchmark():
    names = ("A: plateau + spikes", "B: spikes only (no edge)", "C: noise only (no edge)")
    res = [_summary(nm, r) for nm, r in zip(names, _pool([(True, 3), (False, 3), (False, 0)]), strict=True)]
    a, b, c = res
    failures = []
    if a["rp_plateau"]["ci"][0] < 0.90:
        failures.append(f"A: plateau selected, lower limit {a['rp_plateau']['ci'][0]:.4f} < 0.90")
    for s in (a, b):
        if s["rp_spike"]["ci"][1] > 0.05:
            failures.append(f"{s['scenario']}: spike selected, upper limit {s['rp_spike']['ci'][1]:.4f} > 0.05")
    out = os.environ.get("RLAB_P7_RESULTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "benchmark.json").write_text(json.dumps(
            {"reps": REPS, "grid": S, "budget": BUDGET, "seed": SEED, "rows": res,
             "key_metric_no_edge": {"B_any_selected": b["rp_any"], "C_any_selected": c["rp_any"],
                                    "B_mean_selected": b["n_selected_mean"], "C_mean_selected": c["n_selected_mean"]}},
            indent=1))
    assert not failures, "\n".join(failures)
