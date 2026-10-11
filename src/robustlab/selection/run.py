"""Run P7 on a round: gates, one representative per region, integrated ranking, budget (P7_PLAN §3-§5).

- Inputs: the P4 filter decisions, the P5 robustness values and regions, and the P6 statistics of the same round
  and filter execution. Only Discovery-period values (stages FILTER / ROBUSTNESS / STATISTICS) are read.
- Every P4-passing candidate gets one SELECTION decision (SELECTED / NOT_SELECTED / FAIL) with reason codes,
  warning codes, gate values and the component name and version. P4 failures are counted, not decided again.
- SELECTION decisions carry sel_id and leave filter_id empty, so P4-P6 queries by filter_id never see them.
- DSR is used for relative ranking only (R4, C62). Being selected is not evidence of an edge (P7_PLAN §6.1).
- Idempotent per (round, filter, robustness, statistics, profile, selector). Append-only.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from robustlab.config.loader import ConfigError, load_selection_profile
from robustlab.core import ids
from robustlab.selection import core
from robustlab.storage.db import Database, now_iso

COMPONENT = "selection"
STAGE = "SELECTION"
SAMPLE_ROLE = "DISCOVERY_IN_SAMPLE"
EXIT_OK, EXIT_USER = 0, 1
# P7 benchmark (docs/research/p7_validation/RESULTS.md): share of no-edge synthetic grids with >= 1 selection
KNOWN_LIMITATIONS = (
    "P7 cannot tell a smooth chance mound (neighbouring passes that look alike by chance) from a real plateau. "
    "In the P7 benchmark with NO edge, at least one candidate was selected in {null_c} of grids (noise only) and "
    "{null_b} (noise + isolated spikes); see docs/research/p7_validation/RESULTS.md.",
    "Being selected is not evidence of an edge. Selection only narrows the candidates for P8 (new periods, fixed "
    "parameters) and P10 (null-hypothesis test of the whole pipeline).",
    "All inputs are DISCOVERY_IN_SAMPLE values; DSR is used for relative ranking only (C62) and inherits the P6 "
    "known limitations.",
)
NULL_SELECTION_RATE = {"null_c": "72.8% (95% CI 68.7-76.7%, mean 1.09 selected)",
                       "null_b": "73.0% (68.9-76.9%, mean 1.12)"}
NOTE = ("DISCOVERY_IN_SAMPLE selection: the candidates were chosen with values from the period they were optimized "
        "in. Being selected is not evidence of an edge; P8 and P10 are the checks against chance.")


class SelectError(ValueError):
    pass


@dataclass
class SelectOutcome:
    exit_code: int
    status: str
    message: str = ""
    sel_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def known_limitations() -> list[str]:
    return [t.format(**NULL_SELECTION_RATE) for t in KNOWN_LIMITATIONS]


def _load(db: Database, round_id: str, filter_id: str, robust_id: str, stat_id: str) -> dict[str, Any]:
    fx = db.one("SELECT * FROM filter_execution WHERE filter_id = ?", (filter_id,))
    rx = db.one("SELECT * FROM robustness_execution WHERE robust_id = ?", (robust_id,))
    sx = db.one("SELECT * FROM statistics_execution WHERE stat_id = ?", (stat_id,))
    for name, row, key in (("filter execution", fx, filter_id), ("robustness execution", rx, robust_id),
                           ("statistics execution", sx, stat_id)):
        if row is None:
            raise SelectError(f"unknown {name} {key}")
    for name, row in (("filter", fx), ("robustness", rx), ("statistics", sx)):
        if row["round_id"] != round_id:
            raise SelectError(f"{name} execution belongs to round {row['round_id']}, not {round_id}")
    for name, row in (("robustness", rx), ("statistics", sx)):
        if row["filter_id"] != filter_id:
            raise SelectError(f"{name} execution used filter {row['filter_id']}, not {filter_id}")
    if sx["sample_role"] != SAMPLE_ROLE:
        raise SelectError(f"statistics execution {stat_id} is {sx['sample_role']}, not {SAMPLE_ROLE}")
    rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (round_id,))
    p4 = {r["candidate_id"]: r for r in db.all(
        "SELECT candidate_id, outcome, warning_codes_json FROM decision WHERE filter_id = ? AND stage = 'FILTER'",
        (filter_id,))}
    p5, p6 = {}, {}
    for r in db.all("SELECT candidate_id, stage, values_json FROM evaluation WHERE robust_id = ?", (robust_id,)):
        if r["stage"] != "ROBUSTNESS":
            raise SelectError(f"robustness execution {robust_id} has a {r['stage']} evaluation")
        p5[r["candidate_id"]] = json.loads(r["values_json"])
    for r in db.all("SELECT candidate_id, stage, values_json FROM evaluation WHERE stat_id = ?", (stat_id,)):
        v = json.loads(r["values_json"])
        if r["stage"] != "STATISTICS" or v.get("sample_role") != SAMPLE_ROLE:
            raise SelectError(f"statistics execution {stat_id} has a non-Discovery evaluation")
        p6[r["candidate_id"]] = v
    regions = [dict(r) for r in db.all("SELECT region_id, medoid_candidate_id, members_json FROM parameter_region "
                                       "WHERE robust_id = ? ORDER BY region_id", (robust_id,))]
    return {"fx": fx, "rx": rx, "sx": sx, "round": rnd, "p4": p4, "p5": p5, "p6": p6, "regions": regions}


def _warnings(cd: str, inp: dict[str, Any], nb: core.Neighbourhood, prof: Any) -> list[str]:
    w: list[str] = []
    v6, v5 = inp["p6"].get(cd, {}), inp["p5"][cd]
    dsr = (v6.get("dsr") or {}).get("dsr_conservative")
    if dsr is not None and dsr < prof.dsr_warn_below:
        w.append("LOW_DSR")
    for code in ("SERIAL_DEPENDENCE_HIGH", "BOOTSTRAP_SENSITIVITY"):
        if code in v6.get("warnings", []):
            w.append(code)
    b = v6.get("bootstrap") or {}
    ci = (b.get("stationary") or {}).get("sharpe_ci") if b.get("status") == "OK" else None
    if ci is not None and ci[0] <= 0 <= ci[1]:
        w.append("SHARPE_CI_INCLUDES_ZERO")
    if v5.get("edge"):
        w.append("EDGE")
    if nb.no_trade:
        w.append("NO_TRADE_NEIGHBOURS")
    sub = v5.get("subperiod") or {}
    share, worst = sub.get("positive_year_share"), sub.get("worst_year_return")
    if (share is not None and share < 0.5) or (worst is not None and worst < 0):
        w.append("SUBPERIOD_INCONSISTENT")
    w += [c for c in json.loads(inp["p4"][cd]["warning_codes_json"]) if c not in w]
    return w


def _rank_input(cd: str, inp: dict[str, Any]) -> tuple[float | None, float | None]:
    """(conservative DSR, resampled max DD p95) from P6; the Robust Score comes from the R3 neighbourhood."""
    v6 = inp["p6"].get(cd, {})
    b = v6.get("bootstrap") or {}
    dd = b["stationary"]["max_dd_distribution"]["p95"] if b.get("status") == "OK" else None
    return (v6.get("dsr") or {}).get("dsr_conservative"), dd


def summarize(db: Database, sel_id: str) -> dict[str, Any]:
    sx = db.one("SELECT * FROM selection_execution WHERE sel_id = ?", (sel_id,))
    if sx is None:
        raise SelectError(f"unknown selection execution {sel_id}")
    decs = [(r["candidate_id"], r["outcome"], json.loads(r["reason_codes_json"]), json.loads(r["warning_codes_json"]),
             json.loads(r["details_json"])) for r in db.all(
        "SELECT * FROM decision WHERE sel_id = ? ORDER BY candidate_id", (sel_id,))]
    reasons = Counter(code for _, _, rs, _, _ in decs for code in rs)
    warns = Counter(code for _, _, _, ws, _ in decs for code in ws)
    selected = []
    for cd in json.loads(sx["selected_json"]):
        d = next(x for x in decs if x[0] == cd)
        det = d[4]
        selected.append({"candidate_id": cd, "rank": det["rank"]["selected_rank"], "params": det["params"],
                         "layer": det["rank"]["layer"], "mean_rank": det["rank"]["mean_rank"],
                         "robust_score_r3": det["rank"]["inputs"]["robust_score"],
                         "dsr_conservative": det["rank"]["inputs"]["dsr"],
                         "max_dd_p95": det["rank"]["inputs"]["max_dd_p95"], "warnings": d[3]})
    return {
        "sel_id": sel_id, "round_id": sx["round_id"], "filter_id": sx["filter_id"], "robust_id": sx["robust_id"],
        "stat_id": sx["stat_id"], "selector": f"{sx['selector_name']} {sx['selector_version']}",
        "profile": f"{sx['profile_name']} {sx['profile_hash'][:16]}", "budget": sx["budget"],
        "candidates": sx["n_candidates"], "eligible_p4_pass": sx["n_eligible"], "gate_pass": sx["n_gate_pass"],
        "representatives": sx["n_representatives"], "selected_count": sx["n_selected"],
        "outcome_reason": sx["outcome_reason"], "revision_no": sx["revision_no"],
        "outcomes": dict(sorted(Counter(o for _, o, _, _, _ in decs).items())),
        "reason_counts": dict(sorted(reasons.items())), "warning_counts": dict(sorted(warns.items())),
        "selected": selected, "known_limitations": json.loads(sx["known_limitations_json"]), "note": NOTE,
    }


def run_select(workspace: Path, round_id: str, filter_id: str, robust_id: str, stat_id: str,
               profiles_path: Path, profile_name: str = "default") -> SelectOutcome:
    workspace = workspace.resolve()
    path = workspace / "robustlab.sqlite"
    if not path.is_file():
        return SelectOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database at {path}")
    try:
        prof, prof_hash = load_selection_profile(profiles_path, profile_name)
    except ConfigError as e:
        return SelectOutcome(EXIT_USER, "CONFIG_INVALID", str(e))
    db = Database(path)
    try:
        try:
            inp = _load(db, round_id, filter_id, robust_id, stat_id)
        except SelectError as e:
            return SelectOutcome(EXIT_USER, "INPUT_INVALID", str(e))
        sel_id = ids.content_id("sl", {"round_id": round_id, "filter_id": filter_id, "robust_id": robust_id,
                                       "stat_id": stat_id, "profile_hash": prof_hash, "selector": prof.selector,
                                       "version": core.SELECTION_VERSION})
        if db.one("SELECT 1 FROM selection_execution WHERE sel_id = ?", (sel_id,)):
            return SelectOutcome(EXIT_OK, "ALREADY_DONE", "already selected with the same inputs and settings",
                                 sel_id, summarize(db, sel_id))
        eligible = sorted(cd for cd, r in inp["p4"].items() if r["outcome"] == "PASS")
        missing_boot = [cd for cd in eligible if (inp["p6"].get(cd) or {}).get("bootstrap") is None]
        if missing_boot:
            return SelectOutcome(EXIT_USER, "INPUT_INVALID",
                                 "the statistics execution has no bootstrap for these P4 passes; rerun "
                                 "`rlab stats run` with --candidate for each: " + " ".join(missing_boot[:10]))
        axes = json.loads(inp["round"]["param_space_json"])
        shape = tuple(len(a["values"]) for a in axes)
        scores = np.full(shape, np.nan)
        statuses = np.full(shape, "", dtype=object)
        index_of: dict[str, tuple[int, ...]] = {}
        for cd, v in inp["p5"].items():
            idx = tuple(v["grid_index"])
            index_of[cd] = idx
            statuses[idx] = v["score_status"]
            if v["score_status"] == "OK" and v["score"] is not None:
                scores[idx] = float(v["score"])
        revision_no = db.one("SELECT COUNT(*) AS n FROM selection_execution WHERE round_id = ?", (round_id,))["n"]
        region_of = {}
        for reg in inp["regions"]:
            for cd in json.loads(reg["members_json"]):
                region_of[cd] = reg
        extra = {}
        for cd in eligible:
            ri = _rank_input(cd, inp)
            extra[cd] = (ri[0], ri[1])
        try:
            dec = core.decide(scores, statuses, eligible, index_of,
                              {cd: inp["p5"][cd]["region_status"] for cd in eligible},
                              [(json.loads(r["members_json"]), r["medoid_candidate_id"]) for r in inp["regions"]],
                              extra, prof.budget, prof.peak_ratio_max, prof.robust_score_min_exclusive)
        except ValueError as e:
            return SelectOutcome(EXIT_USER, "INPUT_INVALID", str(e))
        per, passed, reps, redundant, sel = dec.per, dec.passed, dec.representatives, dec.redundant, dec.selection
        for cd in eligible:
            per[cd]["warnings"] = _warnings(cd, inp, per[cd]["nb"], prof)
        ranked = {r.candidate_id: r for r in sel.ranking}
        cands = {r["candidate_id"]: json.loads(r["params_json"]) for r in db.all(
            "SELECT candidate_id, params_json FROM candidate WHERE candidate_id IN (%s)" % ",".join("?" * len(eligible)),
            tuple(eligible))} if eligible else {}
        now = now_iso()
        decisions = []
        for cd in eligible:
            p = per[cd]
            warnings = list(p["warnings"])
            r = ranked.get(cd)
            if r is not None and r.missing:
                warnings.append("RANK_INPUT_MISSING")
            if revision_no:
                warnings.append("SELECTION_REVISED")
            if p["reasons"]:
                outcome, reasons = "FAIL", p["reasons"]
            elif cd in redundant:
                outcome, reasons = "NOT_SELECTED", ["REDUNDANT_IN_REGION"]
            elif cd in sel.selected:
                outcome, reasons = "SELECTED", []
            else:
                outcome, reasons = "NOT_SELECTED", [sel.not_selected[cd]]
            assert not (set(reasons) & core.DSR_CODES)  # R4: DSR never decides
            v5 = inp["p5"][cd]
            nb = p["nb"]
            ri = p["rank_input"]
            details = {
                "params": cands.get(cd), "grid_index": list(index_of[cd]), "sample_role": SAMPLE_ROLE,
                "inputs": {"filter_id": filter_id, "robust_id": robust_id, "stat_id": stat_id},
                "profile": profile_name, "profile_hash": prof_hash, "profile_values": prof.model_dump(mode="json"),
                "gates": [asdict(g) for g in p["gates"]],
                "neighbourhood": {"r3": asdict(nb), "floor": core.neighbour_floor(len(shape)),
                                  "p5": {"robust_score": v5.get("robust_score"),
                                         "robust_score_reason": v5.get("robust_score_reason"),
                                         "peak_ratio": v5.get("peak_ratio"), "neighbours": v5.get("neighbours"),
                                         "neighbour_status_counts": v5.get("neighbour_status_counts")},
                                  "note": "r3 = 独自拡張 (no-trade neighbours not counted, C69); p5 = standard"},
                "region_id": (region_of.get(cd) or {}).get("region_id"),
                "representative_of_region": redundant.get(cd, cd if cd in reps else None),
                "rank": {"inputs": {"robust_score": ri.robust_score, "dsr": ri.dsr, "max_dd_p95": ri.max_dd_p95},
                         "layer": r.layer if r else None, "mean_rank": r.mean_rank if r else None,
                         "ranks": r.ranks if r else None, "missing": r.missing if r else None,
                         "selected_rank": sel.selected.index(cd) + 1 if cd in sel.selected else None,
                         "method": "pareto layers -> mean of ranks -> robust, max DD, DSR rank -> candidate_id (C68)"},
            }
            decisions.append({
                "decision_id": ids.content_id("dc", {"sel_id": sel_id, "candidate_id": cd}),
                "candidate_id": cd, "stage": STAGE, "outcome": outcome, "component_name": COMPONENT,
                "component_version": f"{core.SELECTOR} {core.SELECTION_VERSION}",
                "reason_codes_json": json.dumps(reasons), "warning_codes_json": json.dumps(warnings),
                "details_json": json.dumps(details, sort_keys=True, default=str), "filter_id": None,
                "sel_id": sel_id, "created_at": now,
            })
        reason = None
        if not eligible:
            reason = "NO_ELIGIBLE: no candidate passed P4"
        elif not passed:
            reason = "NO_GATE_PASS: every P4 pass failed a P7 gate"
        with db.tx():
            for code, (cat, desc, sev) in core.REASON_CODES.items():
                db.insert("reason_code", {"code": code, "category": cat, "description": desc, "severity": sev},
                          if_absent=True)
            db.insert("selection_execution", {
                "sel_id": sel_id, "round_id": round_id, "filter_id": filter_id, "robust_id": robust_id,
                "stat_id": stat_id, "profile_name": profile_name, "profile_hash": prof_hash,
                "profile_json": ids.canonical_json(prof.model_dump(mode="json")), "selector_name": prof.selector,
                "selector_version": core.SELECTION_VERSION, "budget": prof.budget,
                "n_candidates": len(inp["p4"]), "n_eligible": len(eligible), "n_gate_pass": len(passed),
                "n_representatives": len(reps), "n_selected": len(sel.selected),
                "selected_json": json.dumps(sel.selected), "outcome_reason": reason, "revision_no": revision_no,
                "known_limitations_json": json.dumps(known_limitations()), "created_at": now,
            })
            for d in decisions:
                db.insert("decision", d)
        return SelectOutcome(EXIT_OK, "DONE", reason or "", sel_id, summarize(db, sel_id))
    finally:
        db.close()


def selection_of(db: Database, candidate_id: str, sel_id: str | None = None) -> dict[str, Any] | None:
    """The latest (or the given) SELECTION decision of a candidate, for why-not."""
    if sel_id:
        d = db.one("SELECT * FROM decision WHERE candidate_id = ? AND sel_id = ? AND stage = 'SELECTION'",
                   (candidate_id, sel_id))
    else:
        d = db.one("SELECT * FROM decision WHERE candidate_id = ? AND stage = 'SELECTION' "
                   "ORDER BY created_at DESC, sel_id DESC LIMIT 1", (candidate_id,))
    if d is None:
        return None
    return {"sel_id": d["sel_id"], "outcome": d["outcome"], "reasons": json.loads(d["reason_codes_json"]),
            "warnings": json.loads(d["warning_codes_json"]), "details": json.loads(d["details_json"]),
            "component": f"{d['component_name']} {d['component_version']}"}
