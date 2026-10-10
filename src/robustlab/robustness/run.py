"""Run the robustness evaluation over every candidate of a filtered round (P5_PLAN §2-§4, C56-C60).

- Inputs: the P3 pass metrics and daily Parquet of the jobs the filter execution used, and the P4 decisions.
- Every candidate gets values, whether it passed P4 or not (C59). No pass/fail is decided here (N7).
- Idempotent per (round, filter execution, score, config, version). Append-only.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from robustlab.core import ids
from robustlab.robustness import grid_stats, subperiod
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso

COMPONENT = "robustness"
SCORE_NAME = "daily_sharpe"
CONFIG = {"neighbourhood": "chebyshev_1_no_padding", "quantile": grid_stats.ROBUST_QUANTILE,
          "quantile_method": grid_stats.QUANTILE_METHOD, "region": "filter_pass_full_connectivity",
          "edge_rule": "neighbours < full/2", "score": SCORE_NAME}
EXIT_OK, EXIT_USER = 0, 1


class RobustError(ValueError):
    pass


@dataclass
class RobustOutcome:
    exit_code: int
    status: str
    message: str = ""
    robust_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def _axes(rnd: Any) -> list[dict[str, Any]]:
    return json.loads(rnd["param_space_json"])


def _index(axes: list[dict[str, Any]], params: dict[str, Any]) -> tuple[int, ...]:
    out = []
    for a in axes:
        v = float(params[a["name"]])
        vals = [float(x) for x in a["values"]]
        i = min(range(len(vals)), key=lambda k: abs(vals[k] - v))
        if not np.isclose(vals[i], v, rtol=1e-9, atol=1e-9):
            raise RobustError(f"{a['name']}={params[a['name']]} is not on the round's grid")
        out.append(i)
    return tuple(out)


def load_inputs(db: Database, store: ArtifactStore, filter_id: str) -> dict[str, Any]:
    fx = db.one("SELECT * FROM filter_execution WHERE filter_id = ?", (filter_id,))
    if fx is None:
        raise RobustError(f"unknown filter execution {filter_id}")
    rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (fx["round_id"],))
    job_ids = json.loads(fx["input_job_ids_json"])
    marks = ",".join("?" for _ in job_ids)
    rows = db.all(f"SELECT p.candidate_id, p.run_id, p.metrics_json, c.params_json, d.outcome FROM pass_result p "
                  f"JOIN candidate c USING (candidate_id) JOIN decision d ON d.candidate_id = p.candidate_id "
                  f"AND d.filter_id = ? WHERE p.job_id IN ({marks})", (filter_id, *job_ids))
    daily: dict[tuple, tuple[list[str], list[float]]] = {}
    axes = _axes(rnd)
    names = [a["name"] for a in axes]
    for jid in job_ids:
        art = db.one("SELECT sha256 FROM opt_job_artifact WHERE job_id = ? AND role = 'daily_parquet'", (jid,))
        if art is None:
            raise RobustError(f"job {jid} has no daily Parquet")
        t = pq.read_table(io.BytesIO(store.read(art["sha256"]))).to_pydict()
        for k in range(len(t["date"])):
            key = tuple(float(t[n][k]) for n in names)
            ds, es = daily.setdefault(key, ([], []))
            ds.append(t["date"][k])
            es.append(float(t["equity_close"][k]))
    return {"fx": fx, "round": rnd, "rows": rows, "daily": daily, "axes": axes, "job_ids": job_ids}


def summarize(db: Database, robust_id: str) -> dict[str, Any]:
    rx = db.one("SELECT * FROM robustness_execution WHERE robust_id = ?", (robust_id,))
    if rx is None:
        raise RobustError(f"unknown robustness execution {robust_id}")
    vals = [json.loads(r["values_json"]) for r in db.all(
        "SELECT values_json FROM evaluation WHERE robust_id = ?", (robust_id,))]
    robust = sorted(v["robust_score"] for v in vals if v["robust_score"] is not None)
    gaps = sorted(v["peak_gap"] for v in vals if v["peak_gap"] is not None)

    def q(xs: list[float], p: float) -> float | None:
        return round(float(np.percentile(xs, p, method="inverted_cdf")), 6) if xs else None

    regions = [dict(r) for r in db.all("SELECT region_id, member_count, medoid_candidate_id FROM parameter_region "
                                       "WHERE robust_id = ? ORDER BY member_count DESC", (robust_id,))]
    region_status: dict[str, int] = {}
    edge = 0
    pos_share = []
    for v in vals:
        region_status[v["region_status"]] = region_status.get(v["region_status"], 0) + 1
        edge += bool(v["edge"])
        if v["subperiod"].get("positive_year_share") is not None:
            pos_share.append(v["subperiod"]["positive_year_share"])
    return {
        "robust_id": robust_id, "round_id": rx["round_id"], "filter_id": rx["filter_id"],
        "component": f"{rx['component_name']} {rx['component_version']}", "score": rx["score_name"],
        "candidates": rx["n_candidates"], "score_status_counts": json.loads(rx["status_counts_json"]),
        "robust_score_quantiles": {"min": q(robust, 0), "p25": q(robust, 25), "median": q(robust, 50),
                                   "p75": q(robust, 75), "max": q(robust, 100), "n_finite": len(robust)},
        "peak_gap_quantiles": {"median": q(gaps, 50), "p90": q(gaps, 90), "max": q(gaps, 100)},
        "axis_sensitivity": json.loads(rx["axis_sensitivity_json"]), "edge_candidates": edge,
        "regions": regions, "regions_reason": rx["regions_reason"], "region_status_counts": region_status,
        "positive_year_share_distribution": {str(k): pos_share.count(k) for k in sorted(set(pos_share))},
        "config": json.loads(rx["config_json"]),
    }


def run_robust(workspace: Path, round_id: str, filter_id: str) -> RobustOutcome:
    workspace = workspace.resolve()
    path = workspace / "robustlab.sqlite"
    if not path.is_file():
        return RobustOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database at {path}")
    db = Database(path)
    try:
        store = ArtifactStore(workspace / "artifacts", db)
        fx = db.one("SELECT round_id FROM filter_execution WHERE filter_id = ?", (filter_id,))
        if fx is None:
            return RobustOutcome(EXIT_USER, "UNKNOWN_FILTER", f"unknown filter execution {filter_id}")
        if fx["round_id"] != round_id:
            return RobustOutcome(EXIT_USER, "FILTER_ROUND_MISMATCH",
                                 f"filter {filter_id} belongs to round {fx['round_id']}, not {round_id}")
        robust_id = ids.content_id("rb", {"round_id": fx["round_id"], "filter_id": filter_id, "config": CONFIG,
                                          "version": grid_stats.GRID_STATS_VERSION})
        if db.one("SELECT 1 FROM robustness_execution WHERE robust_id = ?", (robust_id,)):
            return RobustOutcome(EXIT_OK, "ALREADY_DONE", "already evaluated with the same filter and settings",
                                 robust_id, summarize(db, robust_id))
        try:
            inp = load_inputs(db, store, filter_id)
        except RobustError as e:
            return RobustOutcome(EXIT_USER, "INPUT_INVALID", str(e))
        rnd, axes = inp["round"], inp["axes"]
        names = [a["name"] for a in axes]
        shape = tuple(len(a["values"]) for a in axes)
        scores = np.full(shape, np.nan)
        statuses = np.full(shape, "", dtype=object)
        passed = np.zeros(shape, dtype=bool)
        cell: dict[tuple[int, ...], dict[str, Any]] = {}
        status_counts: dict[str, int] = {}
        for r in inp["rows"]:
            params = json.loads(r["params_json"])
            idx = _index(axes, params)
            m = json.loads(r["metrics_json"])
            s, st, flags = grid_stats.score_of(m)
            scores[idx], statuses[idx], passed[idx] = s, st, r["outcome"] == "PASS"
            status_counts[st] = status_counts.get(st, 0) + 1
            key = tuple(float(params[n]) for n in names)
            cell[idx] = {"candidate_id": r["candidate_id"], "run_id": r["run_id"], "flags": flags, "key": key}
        res = grid_stats.compute(scores, statuses.astype(str), passed, names)
        deposit = float(json.loads(rnd["tester_settings_json"])["deposit"])
        now = now_iso()
        region_rows = []
        region_ids = {}
        for reg in res.regions:
            members = [cell[c]["candidate_id"] for c in reg["members"]]
            rid = ids.content_id("rg", {"robust_id": robust_id, "members": sorted(members)})
            region_ids[reg["index"]] = rid
            region_rows.append({"region_id": rid, "robust_id": robust_id, "round_id": rnd["round_id"],
                                "method": "GRID_CONNECTED", "method_version": grid_stats.GRID_STATS_VERSION,
                                "member_count": reg["size"], "medoid_candidate_id": cell[reg["medoid"]]["candidate_id"],
                                "members_json": json.dumps(sorted(members)), "created_at": now})
        evals = []
        for idx, v in res.per_cell.items():
            c = cell[idx]
            ds, es = inp["daily"].get(c["key"], ([], []))
            order = sorted(range(len(ds)), key=lambda k: ds[k])
            sub = subperiod.yearly([ds[k] for k in order], [es[k] for k in order], deposit)
            values = v | {"score_flags": c["flags"], "grid_index": list(idx), "subperiod": sub,
                          "region_id": region_ids.get(v["region_index"]), "score_name": SCORE_NAME,
                          "config": CONFIG, "version": grid_stats.GRID_STATS_VERSION}
            evals.append({
                "evaluation_id": ids.content_id("ev", {"robust_id": robust_id, "candidate_id": c["candidate_id"]}),
                "candidate_id": c["candidate_id"], "stage": "ROBUSTNESS", "evaluator_name": COMPONENT,
                "evaluator_version": grid_stats.GRID_STATS_VERSION, "config_hash": ids.sha256_hex(ids.canonical_json(CONFIG)),
                "input_run_ids_json": json.dumps([c["run_id"]]), "input_hash": ids.sha256_hex(ids.canonical_json(
                    {"filter_id": filter_id, "job_ids": inp["job_ids"]})),
                "values_json": json.dumps(values, sort_keys=True, default=str), "filter_id": filter_id,
                "robust_id": robust_id, "created_at": now,
            })
        with db.tx():
            db.insert("robustness_execution", {
                "robust_id": robust_id, "round_id": rnd["round_id"], "filter_id": filter_id, "score_name": SCORE_NAME,
                "component_name": COMPONENT, "component_version": grid_stats.GRID_STATS_VERSION,
                "config_json": ids.canonical_json(CONFIG), "axis_sensitivity_json": json.dumps(res.axis_sensitivity),
                "status_counts_json": json.dumps(dict(sorted(status_counts.items()))), "n_candidates": len(evals),
                "n_regions": len(region_rows), "regions_reason": res.regions_reason, "created_at": now,
            })
            for row in region_rows:
                db.insert("parameter_region", row)
            for e in evals:
                db.insert("evaluation", e)
        return RobustOutcome(EXIT_OK, "DONE", "", robust_id, summarize(db, robust_id))
    finally:
        db.close()


def grid_map(db: Database, robust_id: str, metric: str = "score") -> list[str]:
    """Text map of a 2-axis round: rows = second axis, columns = first axis (P5_PLAN §2.1-8)."""
    rx = db.one("SELECT round_id FROM robustness_execution WHERE robust_id = ?", (robust_id,))
    if rx is None:
        raise RobustError(f"unknown robustness execution {robust_id}")
    rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (rx["round_id"],))
    axes = _axes(rnd)
    if len(axes) != 2:
        raise RobustError(f"the map needs exactly 2 optimized inputs (this round has {len(axes)})")
    vals = {tuple(v["grid_index"]): v for v in (json.loads(r["values_json"]) for r in db.all(
        "SELECT values_json FROM evaluation WHERE robust_id = ?", (robust_id,)))}
    key = "robust_score" if metric == "robust" else "score"
    finite = sorted(v[key] for v in vals.values() if v[key] is not None)
    shades = " .:-=+*#%@"

    def ch(v: dict[str, Any] | None) -> str:
        if v is None:
            return " "
        if v["region_status"] == "IN_REGION":
            return "R"
        if v["region_status"] == "ISOLATED_PASS":
            return "P"
        if v[key] is None:
            if v["score"] is not None:  # own score is fine, the neighbourhood pulls the robust score to -inf
                return "x"
            return {"NO_TRADES": "_", "BANKRUPTCY": "B", "STOP_OUT": "S"}.get(v["score_status"], "?")
        rank = sum(1 for x in finite if x <= v[key]) / len(finite)
        return shades[min(len(shades) - 1, int(rank * len(shades)))]

    a0, a1 = axes
    lines = [(f"{metric} map: columns {a0['name']} {a0['values'][0]}..{a0['values'][-1]}, "
              f"rows {a1['name']} {a1['values'][0]}..{a1['values'][-1]}")]
    for j in reversed(range(len(a1["values"]))):
        lines.append(f"{a1['values'][j]!s:>6} |" + "".join(ch(vals.get((i, j))) for i in range(len(a0["values"]))))
    lines.append(" " * 7 + "+" + "-" * len(a0["values"]))
    lines.append("legend: ' .:-=+*#%@' = rank of the value among finite candidates (low -> high); "
                 "_ no trades, B bankruptcy, S stop-out, ? not computable, x robust score -inf because of neighbours, "
                 "R in a region / P isolated P4 pass")
    return lines
