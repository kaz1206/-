"""One study-A round through the PRODUCTION decision functions, in the order and with the seeds of the production
`rlab filter / robust / stats / select / validate` (SPEC §5, §10 A6-A7). Every candidate leaves a trace row.

Same functions: P4 metrics.pass_metrics.compute_pass + filtering.gates.evaluate; P5 robustness.grid_stats +
subperiod.yearly; P6 stats.dsr (sr0, deflated) + stats.bootstrap.evaluate for P4 passes and P5 medoids, per-candidate
seed sha256(seed|candidate_id); P7 selection.core.decide with the default selection profile; P8a
stats.validation.evidence (seed sha256(plan_id|candidate_id)) + holm over the selected candidates, ruin / pooled
profit / extra cost gates as in validation/run.py. Not replicable on synthetic data (recorded): the P8a
reproducibility re-run (always PASS), the exposure ledger. Approximation (recorded): V[SR] from this round's 60 results.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.config.loader import load_profile, load_selection_profile
from robustlab.core import ids
from robustlab.filtering import gates as p4
from robustlab.metrics.pass_metrics import compute_pass
from robustlab.robustness import grid_stats, subperiod
from robustlab.selection import core
from robustlab.stat_eval.run import candidate_seed
from robustlab.stats import bootstrap, dsr
from robustlab.stats import validation as ev
from robustlab.studya import prices, t1
from robustlab.validation import costs

EVALUATE_VERSION = "ea1"
REPO = Path(__file__).resolve().parents[3]


@dataclass
class Setup:
    cfg: dict[str, Any]
    cfg_hash: str
    pm: prices.PriceModel
    days: list[dt.date]
    windows: dict[str, t1.Window]
    lookbacks: list[int]
    holds: list[int]
    profile: Any
    sel: Any
    years: float
    spd: int
    n_bars: int


def load_config(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return yaml.safe_load(raw), ids.sha256_hex(raw)


def _axis(a: dict[str, Any]) -> list[int]:
    return list(range(int(a["start"]), int(a["stop"]) + 1, int(a["step"])))


def setup(cfg_path: Path, profiles: Path | None = None, sel_profiles: Path | None = None) -> Setup:
    cfg, h = load_config(cfg_path)
    pm = prices.PriceModel(**cfg["price"])
    cal = cfg["calendar"]
    d_from, d_to = (dt.date.fromisoformat(str(x)) for x in cal["discovery"])
    first = prices.weekdays_before(d_from, int(cal["warmup_weekdays"]))
    v1_to = dt.date.fromisoformat(str(cal["v1"][1]))
    days = prices.weekdays(first, v1_to)
    index = {d: i for i, d in enumerate(days)}
    spd = pm.bars_per_day * pm.steps_per_bar

    def window(a: Any, b: Any) -> t1.Window:
        a, b = dt.date.fromisoformat(str(a)), dt.date.fromisoformat(str(b))
        i0 = index[min(d for d in days if d >= a)]
        i1 = index[max(d for d in days if d < b)] + 1
        return t1.Window(i0 * spd, i1 * spd)

    windows = {"D": window(*cal["discovery"]), "V0": window(*cal["v0"]), "V1": window(*cal["v1"])}
    profile, _ = load_profile(profiles or REPO / "configs" / "strategy_profiles.yaml", cfg["profile"])
    sel, _ = load_selection_profile(sel_profiles or REPO / "configs" / "selection_profiles.yaml",
                                    cfg["selection_profile"])
    g = cfg["grid"]
    return Setup(cfg, h, pm, days, windows, _axis(g["InpLookback"]), _axis(g["InpHoldBars"]), profile, sel,
                 (d_to - d_from).days / 365.25, spd, len(days) * pm.bars_per_day)


def round_path(st: Setup, base_seed: int, i: int, edge: str | None = None, scale: float = 0.0,
               edge_seed: int | None = None) -> np.ndarray:
    steps = prices.base_steps(st.n_bars, st.pm, base_seed, i)
    if edge is not None:
        e = st.cfg["edges"][edge]
        unit = prices.edge_unit(e["kind"], st.n_bars, st.pm.steps_per_bar, int(edge_seed), i,
                                persistence_bars=float(e.get("persistence_bars", 0)),
                                half_life_bars=float(e.get("half_life_bars", 0)))
        steps = steps + scale * unit
    return prices.path(steps, float(st.cfg["account"]["start_price"]))


def run_cell(st: Setup, p: np.ndarray, w: t1.Window, lookback: int, hold: int, full: bool = True) -> t1.TestResult:
    a = st.cfg["account"]
    return t1.run_test(p, w, lookback, hold, lots=float(st.cfg["lots"]), spread=float(a["spread"]),
                       contract=float(a["contract"]), deposit=float(a["deposit"]), leverage=float(a["leverage"]),
                       spb=st.pm.steps_per_bar, spd=st.spd, days=st.days, full=full,
                       stop_out_pct=float(a["stop_out_pct"]))


def normalized(lookback: int, hold: int, lots: float) -> dict[str, Any]:
    """Normalized params as production stores them (int -> int, double -> 15-significant-digit text)."""
    return dict(sorted({"InpLookback": lookback, "InpHoldBars": hold, "InpLots": f"{lots:.15g}"}.items()))


@dataclass(frozen=True)
class RoundIds:
    """IDs and seeds of one round: production rules (SPEC §10 A7)."""
    cd: dict[tuple[int, int], str]
    stat_seed: int
    plan_id: str


def synthetic_ids(st: Setup, tag: str, i: int) -> RoundIds:
    lots = float(st.cfg["lots"])
    cd = {(L, H): ids.candidate_id(st.cfg["strategy_version"], "EURUSD", "H4", normalized(L, H, lots))
          for L in st.lookbacks for H in st.holds}
    stat_seed = int(hashlib.sha256(f"{tag}|{i}|stats".encode()).hexdigest()[:8], 16)
    plan_id = ids.content_id("vp", {"study": "A", "tag": tag, "round": i})
    return RoundIds(cd, stat_seed, plan_id)


def plan_seed(plan_id: str, cd: str) -> int:
    return int(hashlib.sha256(f"{plan_id}|{cd}".encode()).hexdigest()[:8], 16)


def period_values(res: t1.TestResult, deposit: float, width: float, tick_size: float, tick_value: float) -> dict[str, Any]:
    """As validation/run.py period_values for one single test (no link to other periods)."""
    rets = t1.daily_returns(res, deposit)
    trade = [d for d in res.deals if int(d["type"]) in (0, 1)]
    net = round(sum(costs.deal_pnl(d) for d in trade), 2)
    closes = [d for d in trade if int(d["entry"]) in (1, 2, 3)]
    return {"days": len(rets), "returns": rets, "deals": res.deals, "net_profit": net, "trades": len(closes),
            "bankrupt": float(res.tracking["equity_min"]) <= 0, "stop_out_deals": int(res.tracking["stop_out_deals"]),
            "extra_cost_1x": costs.extra_cost(res.deals, width, tick_size, tick_value, 1.0)}


def evaluate_round(st: Setup, p: np.ndarray, rid: RoundIds, *, n_trials: int | None = None,
                   var_sr: float | None = None, d_results: dict | None = None,
                   v_results: dict | None = None) -> dict[str, Any]:
    """d_results / v_results: precomputed tests (the equivalence check passes the series it gave to the fake
    terminal); otherwise they are simulated here."""
    a = st.cfg["account"]
    dep = float(a["deposit"])
    reps = int(st.cfg["bootstrap_reps"])
    cells = [(L, H) for L in st.lookbacks for H in st.holds]
    cd_of = rid.cd
    D = d_results if d_results is not None else {c: run_cell(st, p, st.windows["D"], *c) for c in cells}
    rows: dict[str, dict[str, Any]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    shape = (len(st.lookbacks), len(st.holds))
    scores = np.full(shape, np.nan)
    statuses = np.full(shape, "", dtype=object)
    passed = np.zeros(shape, dtype=bool)
    index_of: dict[str, tuple[int, int]] = {}
    # P4 (filter run)
    for c in cells:
        cd = cd_of[c]
        idx = (st.lookbacks.index(c[0]), st.holds.index(c[1]))
        index_of[cd] = idx
        r = D[c]
        m, _ = compute_pass(r.stats, r.tracking, r.daily, dep)
        metrics[cd] = m
        dec = p4.evaluate(m, st.profile, st.years)
        s, status, _flags = grid_stats.score_of(m)
        scores[idx], statuses[idx], passed[idx] = s, status, dec.outcome == "PASS"
        ea_closes = sum(1 for x in r.closes if x["kind"] == "EA")
        rows[cd] = {"candidate_id": cd, "InpLookback": c[0], "InpHoldBars": c[1],
                    "d": {"trades": m.get("trades"), "trades_ea": ea_closes, "trades_end_of_test": len(r.closes) - ea_closes,
                          "net_profit": m.get("net_profit"), "days": m.get("days"), "daily_sharpe": m.get("daily_sharpe"),
                          "psr": dec.values.get("psr"), "first_deal_step": r.first_deal_step},
                    "p4": {"outcome": dec.outcome, "reasons": dec.reasons, "warnings": dec.warnings},
                    "score_status": status}
    # P5 (robust run)
    names = list(st.cfg["grid"]["axes"])
    res5 = grid_stats.compute(scores, statuses.astype(str), passed, names)
    region_cds = []
    for reg in res5.regions:
        members = sorted(cd_of[(st.lookbacks[m[0]], st.holds[m[1]])] for m in reg["members"])
        med = cd_of[(st.lookbacks[reg["medoid"][0]], st.holds[reg["medoid"][1]])]
        region_cds.append((members, med))
    region_cds.sort(key=lambda x: x[1])
    for c in cells:
        cd = cd_of[c]
        v = res5.per_cell[index_of[cd]]
        sub = subperiod.yearly([r["date"] for r in D[c].daily], [float(r["equity_close"]) for r in D[c].daily], dep)
        rows[cd]["p5"] = {"robust_score": v.get("robust_score"), "region_status": v.get("region_status"),
                          "peak_ratio": v.get("peak_ratio"), "edge": v.get("edge"),
                          "positive_year_share": sub.get("positive_year_share")}
    # P6 (stats run): N, V, SR0, bootstrap targets = P4 passes + P5 medoids
    ok_sr = [metrics[cd_of[c]]["daily_sharpe"] for c in cells if rows[cd_of[c]]["score_status"] == "OK"]
    V = var_sr if var_sr is not None else (float(np.var(ok_sr, ddof=1)) if len(ok_sr) >= 2 else None)
    N = n_trials if n_trials is not None else int(st.cfg["dsr_n_trials"])
    s0 = dsr.sr0(N, V)
    targets = {cd for cd, r in rows.items() if r["p4"]["outcome"] == "PASS"} | {med for _, med in region_cds}
    p6: dict[str, dict[str, Any]] = {}
    for c in cells:
        cd = cd_of[c]
        m = metrics[cd]
        if rows[cd]["score_status"] != "OK":
            p6[cd] = {"dsr": None, "boot": ({"status": "NOT_COMPUTED"} if cd in targets else None)}
        else:
            ret = t1.daily_returns(D[c], dep)
            boot = bootstrap.evaluate(ret, dep, reps, candidate_seed(rid.stat_seed, cd)) if cd in targets else None
            se = boot["stationary"]["sharpe_se"] if boot and boot["status"] == "OK" else None
            d = dsr.deflated(m["daily_sharpe"], int(m["days"]), m.get("daily_skewness"), m.get("daily_kurtosis"),
                             s0, ret, se)
            p6[cd] = {"dsr": d, "boot": boot}
        dd95 = (p6[cd]["boot"] or {}).get("stationary", {}).get("max_dd_distribution", {}).get("p95") \
            if (p6[cd]["boot"] or {}).get("status") == "OK" else None
        rows[cd]["p6"] = {"dsr_conservative": (p6[cd]["dsr"] or {}).get("dsr_conservative"), "max_dd_p95": dd95,
                          "bootstrap_target": cd in targets}
    # P7 (select run)
    eligible = sorted(cd for cd, r in rows.items() if r["p4"]["outcome"] == "PASS")
    selected: list[str] = []
    if eligible:
        sc = np.full(shape, np.nan)
        for cd, idx in index_of.items():
            if statuses[idx] == "OK":
                sc[idx] = scores[idx]
        extra = {cd: (rows[cd]["p6"]["dsr_conservative"], rows[cd]["p6"]["max_dd_p95"]) for cd in eligible}
        dec7 = core.decide(sc, statuses.astype(str), eligible, index_of,
                           {cd: rows[cd]["p5"]["region_status"] for cd in eligible}, region_cds, extra,
                           st.sel.budget, st.sel.peak_ratio_max, st.sel.robust_score_min_exclusive)
        sel = dec7.selection
        ranked = {r.candidate_id: r for r in sel.ranking}
        for cd in eligible:
            per = dec7.per[cd]
            if per["reasons"]:
                out, reasons = "FAIL", list(per["reasons"])
            elif cd in dec7.redundant:
                out, reasons = "NOT_SELECTED", ["REDUNDANT_IN_REGION"]
            elif cd in sel.selected:
                out, reasons = "SELECTED", []
            else:
                out, reasons = "NOT_SELECTED", [sel.not_selected[cd]]
            r = ranked.get(cd)
            rows[cd]["p7"] = {"outcome": out, "reasons": reasons,
                              "rank": sel.selected.index(cd) + 1 if cd in sel.selected else None,
                              "layer": r.layer if r else None, "mean_rank": r.mean_rank if r else None}
        selected = list(sel.selected)
    # P8a (validate lock + run): selected candidates on V0 and V1, Holm over the selected
    width, ts, tv = float(st.cfg["extra_cost_width"]), float(a["tick_size"]), float(a["tick_value"])
    inv = {v: k for k, v in cd_of.items()}
    pvals: dict[str, float] = {}
    per8: dict[str, dict[str, Any]] = {}
    for cd in selected:
        c = inv[cd]
        pv = {}
        for name in ("V0", "V1"):
            res = (v_results or {}).get((c, name)) or run_cell(st, p, st.windows[name], *c)
            pv[name] = period_values(res, dep, width, ts, tv)
        rng = np.random.Generator(np.random.PCG64(plan_seed(rid.plan_id, cd)))
        e = ev.evidence([pv[n]["returns"] for n in pv], reps, rng)
        pvals[cd] = e["p"]
        per8[cd] = {"periods": pv, "evidence": e}
    rejected = ev.holm(pvals) if pvals else {}
    passers = []
    for cd in selected:
        x = per8[cd]
        pv = x["periods"]
        net = sum(v["net_profit"] for v in pv.values())
        net_1x = net - sum(v["extra_cost_1x"] for v in pv.values())
        gates = {"REPRODUCIBILITY_MISMATCH": "PASS (not replicable on synthetic data)",
                 "VALIDATION_RUIN": "FAIL" if any(v["bankrupt"] or v["stop_out_deals"] for v in pv.values()) else "PASS",
                 "VALIDATION_PERIOD_LOSS": "FAIL" if net <= 0 else "PASS",
                 "VALIDATION_INSUFFICIENT_EVIDENCE": "PASS" if rejected[cd] else "FAIL",
                 "COST_FRAGILE": "FAIL" if net_1x <= 0 else "PASS"}
        reasons = [k for k, g in gates.items() if g == "FAIL"]
        outcome = "PASS" if not reasons else "FAIL"
        if outcome == "PASS":
            passers.append(cd)
        rows[cd]["p8a"] = {"outcome": outcome, "reasons": reasons, "p": x["evidence"]["p"],
                           "holm_rejected": bool(rejected[cd]), "net": round(net, 2), "net_after_cost": round(net_1x, 2),
                           "v0": {"net": pv["V0"]["net_profit"], "trades": pv["V0"]["trades"], "days": pv["V0"]["days"]},
                           "v1": {"net": pv["V1"]["net_profit"], "trades": pv["V1"]["trades"], "days": pv["V1"]["days"]}}
    for cd, r in rows.items():
        r["final"] = "PASS" if cd in passers else "FAIL"
    return {"n_trials": N, "var_sr": V, "sr0": s0.value, "p4_pass": len(eligible),
            "gate_pass": sum(1 for cd in eligible if rows[cd]["p7"]["outcome"] != "FAIL"),
            "selected": len(selected), "p8a_pass": len(passers), "detected": bool(passers),
            "regions": len(region_cds), "regions_reason": res5.regions_reason,
            "price_min": float(p.min()), "price_max": float(p.max()),
            "gross_mean": float(np.mean([metrics[cd_of[c]]["net_profit"] + _spread_paid(D[c], st) for c in cells])),
            "rows": [rows[cd_of[c]] for c in cells]}


def _spread_paid(r: t1.TestResult, st: Setup) -> float:
    """Spread cost of one test: one spread per opened position (S1 uses P&L before this cost)."""
    opens = sum(1 for d in r.deals if d["entry"] == "0" and d["type"] in ("0", "1"))
    return opens * float(st.cfg["account"]["spread"]) * float(st.cfg["lots"]) * float(st.cfg["account"]["contract"])


def finite(x: Any) -> Any:
    return None if isinstance(x, float) and not math.isfinite(x) else x
