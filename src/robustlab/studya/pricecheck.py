"""Price model verification (SPEC §12.5), saved to results/price_model_check.json BEFORE the main measurement.

Separates the two things the multiplicative path (s3-C1) does and does not change:
- the RELATIVE step returns r_t (distribution, volatility clustering, rare large moves): the same random draws and
  the same parameters as the P10 H0 model (checked number for number against robustlab.nulltest.sim.prices with
  the same generator, and the price block against configs/null_archetypes.yaml);
- the ABSOLUTE step width |dp_t|: start * |r_t| in the P10 arithmetic path, p_{t-1} * |r_t| in the multiplicative
  path, i.e. proportional to the price level. The difference is measured on the S paths and recorded.
Also: strength 0 gives the H0 path bit for bit, and every S path and (with the calibration) every P path stays
positive. The P10 code, config and results are only read.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.core import ids
from robustlab.nulltest import sim
from robustlab.studya import evaluate as E
from robustlab.studya import prices

CHECK_VERSION = "pc1"
P10_CONFIG = E.REPO / "configs" / "null_archetypes.yaml"


def _bar_returns(r: np.ndarray, spb: int) -> np.ndarray:
    return r[: len(r) // spb * spb].reshape(-1, spb).sum(axis=1)


def _stats(r: np.ndarray, spb: int) -> dict[str, float]:
    """Relative-return statistics at the 4-hour bar level: sd, excess kurtosis, lag-1 autocorrelation of r and |r|,
    share of |r| > 4 sd (rare large moves)."""
    b = _bar_returns(r, spb)
    sd = float(b.std())
    z = (b - b.mean()) / sd

    def ac(x: np.ndarray) -> float:
        x = x - x.mean()
        return float((x[1:] * x[:-1]).sum() / (x * x).sum())

    return {"bar_sd": sd, "excess_kurtosis": float((z ** 4).mean() - 3), "ac1_r": ac(b), "ac1_abs_r": ac(np.abs(b)),
            "share_abs_gt_4sd": float((np.abs(z) > 4).mean())}


def _q(x: np.ndarray) -> dict[str, float]:
    return {k: float(np.quantile(x, q)) for k, q in (("p01", 0.01), ("p10", 0.1), ("p50", 0.5), ("p90", 0.9),
                                                       ("p99", 0.99))} | {"min": float(x.min()), "max": float(x.max())}


def run(cfg_path: Path, calib_path: Path | None = None, progress=None) -> dict[str, Any]:
    st = E.setup(cfg_path)
    pm, spb, start = st.pm, st.pm.steps_per_bar, float(st.cfg["account"]["start_price"])
    issues: list[str] = []
    out: dict[str, Any] = {"version": CHECK_VERSION, "config_hash": st.cfg_hash, "prices_version": prices.PRICES_VERSION}

    # 1. same parameters and same draws as the P10 H0 model
    p10 = yaml.safe_load(P10_CONFIG.read_text(encoding="utf-8"))
    same_params = p10["price"] == st.cfg["price"] and float(p10["account"]["start_price"]) == start
    m = st.cfg["measurements"]["S"]
    n_days = len(st.days)
    draws = []
    for i in range(5):
        b = prices.base_steps(st.n_bars, pm, int(m["base_seed"]), i)
        q = sim.prices(1, n_days, np.random.default_rng([int(m["base_seed"]), i, 0]), sim.PriceModel(**st.cfg["price"]),
                       start)[0]
        draws.append(float(np.abs(np.diff(q) / start - b).max()))
    out["same_as_p10"] = {"price_block_equal": same_params, "p10_config": "configs/null_archetypes.yaml (read only)",
                          "max_abs_step_return_diff_rounds_0_4": max(draws),
                          "note": "P10 arithmetic increments / start vs study A relative steps, same generator"}
    if not same_params or max(draws) > 1e-12:
        issues.append(f"step returns differ from the P10 H0 model (params equal {same_params}, max diff {max(draws)})")

    # 2. S paths: positivity, relative-return statistics (identical draws) vs absolute widths (old vs new)
    n = int(m["rounds"])
    pos_min, pos_max, neg_arith, rel_new, rel_old = [], [], 0, [], []
    width_ratio, mean_abs_new, mean_abs_old, final_new, final_old = [], [], [], [], []
    for i in range(n):
        r = prices.base_steps(st.n_bars, pm, int(m["base_seed"]), i)
        p = prices.path(r, start)                                    # raises if not positive
        old = np.round(start + start * np.concatenate([[0.0], np.cumsum(r)]), 5)
        pos_min.append(float(p.min()))
        pos_max.append(float(p.max()))
        neg_arith += int(old.min() <= 0)
        if i < 50:   # relative returns realized by each path (after 5-digit rounding)
            rel_new.append(_stats(np.diff(p) / p[:-1], spb))
            if old.min() > 0:
                rel_old.append(_stats(np.diff(old) / old[:-1], spb))
        width_ratio.append(float(np.mean(np.abs(np.diff(p))) / np.mean(np.abs(np.diff(old)))))
        mean_abs_new.append(float(np.mean(np.abs(np.diff(p)))) / 1e-5)
        mean_abs_old.append(float(np.mean(np.abs(np.diff(old)))) / 1e-5)
        final_new.append(float(p[-1]))
        final_old.append(float(old[-1]))
        if progress and (i + 1) % 200 == 0:
            progress(f"S paths {i + 1}/{n}")
    base_stats = [_stats(prices.base_steps(st.n_bars, pm, int(m["base_seed"]), i), spb) for i in range(50)]
    mean = lambda rows: {k: float(np.mean([x[k] for x in rows])) for k in rows[0]}
    out["s_paths"] = {
        "rounds": n, "all_positive": True, "price_min": _q(np.array(pos_min)), "price_max": _q(np.array(pos_max)),
        "relative_returns_bar_level_mean_of_50_rounds": {
            "drawn_r_t": mean(base_stats), "multiplicative_path": mean(rel_new),
            "arithmetic_path_P10_style": mean(rel_old) if rel_old else None},
        "absolute_width": {"mean_abs_step_points_multiplicative": _q(np.array(mean_abs_new)),
                           "mean_abs_step_points_arithmetic": _q(np.array(mean_abs_old)),
                           "ratio_multiplicative_to_arithmetic": _q(np.array(width_ratio))},
        "final_price": {"multiplicative": _q(np.array(final_new)), "arithmetic": _q(np.array(final_old))},
        "arithmetic_paths_reaching_0": neg_arith,
        "reading": "relative returns: the same draws (the multiplicative path reproduces them up to 5-digit "
                   "rounding); absolute widths: proportional to the price level in the multiplicative path, constant "
                   "(start * r_t) in the arithmetic one"}
    rs = out["s_paths"]["relative_returns_bar_level_mean_of_50_rounds"]
    if abs(rs["multiplicative_path"]["bar_sd"] / rs["drawn_r_t"]["bar_sd"] - 1) > 0.01:
        issues.append("the multiplicative path does not reproduce the drawn relative returns")

    # 3. strength 0 = H0 bit for bit (both edges, several rounds)
    zero_ok = all(np.array_equal(E.round_path(st, int(m["base_seed"]), i),
                                 E.round_path(st, int(m["base_seed"]), i, edge, 0.0, int(mm["edge_seed"])))
                  for i in range(3) for edge, mm in ((x["edge"], x) for x in st.cfg["measurements"].values()
                                                     if x.get("edge")))
    out["strength_zero_is_h0"] = zero_ok
    if not zero_ok:
        issues.append("strength 0 does not give the H0 path")

    # 4. P paths: positivity at the calibrated scales
    cp = calib_path or E.REPO / "docs" / "research" / "study_a" / "results" / "calibration.json"
    if cp.exists():
        text = cp.read_text(encoding="utf-8")
        calib = json.loads(text)
        out["calibration_hash"] = ids.sha256_hex(text)
        if calib.get("config_hash") != st.cfg_hash:
            issues.append("the calibration was made with another study_a.yaml")
        pp = {}
        for name, mm in st.cfg["measurements"].items():
            if not mm.get("edge"):
                continue
            scale = float(calib["edges"][mm["edge"]][str(float(mm["strength"]))]["scale"])
            lo, hi, bad = math.inf, 0.0, []
            for i in range(int(mm["rounds"])):
                try:
                    p = E.round_path(st, int(mm["base_seed"]), i, mm["edge"], scale, int(mm["edge_seed"]))
                    lo, hi = min(lo, float(p.min())), max(hi, float(p.max()))
                except ValueError:
                    bad.append(i)
            pp[name] = {"scale": scale, "rounds": int(mm["rounds"]), "non_positive_rounds": bad, "price_min": lo,
                        "price_max": hi}
            if bad:
                issues.append(f"{name}: {len(bad)} paths not positive (rounds {bad[:5]})")
            if progress:
                progress(f"{name}: price {lo:.4f}-{hi:.4f}, non-positive {len(bad)}")
        out["p_paths"] = pp
    else:
        out["p_paths"] = None
        out["calibration_hash"] = None
        issues.append("P paths not checked: no calibration yet (run again after `rlab studya calibrate`)")
    out["issues"] = issues
    out["ok"] = not issues
    return out


def save(out: dict[str, Any], path: Path) -> str:
    text = json.dumps(out, indent=1, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return ids.sha256_hex(text)
