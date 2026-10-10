"""Run the initial filter over every pass of an optimization round and store the results (P4_PLAN §2-§4).

- Only complete rounds are filtered (every chunk has a SUCCEEDED job); the latest successful job of
  each chunk is used.
- Idempotent: the same round, profile content, filter version and input jobs give the same filter_id.
- Changing a threshold creates a new filter execution; nothing is updated or deleted (append-only).
- No new market data is read: only metrics stored by P3 for the round's Discovery period.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from robustlab.config.loader import ConfigError, load_profile
from robustlab.core import ids
from robustlab.filtering import gates
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso

EXIT_OK, EXIT_USER = 0, 1
_SERVER_RE = re.compile(rb"<Server>([^<]*)</Server>")


class FilterError(ValueError):
    pass


@dataclass
class FilterOutcome:
    exit_code: int
    status: str
    message: str = ""
    filter_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def _round_inputs(db: Database, round_id: str) -> tuple[Any, list[str]]:
    rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (round_id,))
    if rnd is None:
        raise FilterError(f"unknown round {round_id}")
    jobs = []
    for chunk in range(rnd["chunk_count"]):
        row = db.one("SELECT job_id FROM opt_job WHERE round_id = ? AND chunk_index = ? AND status = 'SUCCEEDED' "
                     "ORDER BY job_id DESC LIMIT 1", (round_id, chunk))
        if row is None:
            raise FilterError(f"round {round_id} is incomplete: chunk {chunk} has no SUCCEEDED job")
        jobs.append(row["job_id"])
    return rnd, jobs


def _conditions(db: Database, store: ArtifactStore, rnd: Any, job_ids: list[str]) -> dict[str, Any]:
    """The round's standard conditions, recorded with every decision (C52)."""
    tester = json.loads(rnd["tester_settings_json"])
    server = None
    art = db.one("SELECT sha256 FROM opt_job_artifact WHERE job_id = ? AND role = 'report' AND name LIKE '%.xml'",
                 (job_ids[0],))
    if art is not None:
        m = _SERVER_RE.search(store.read(art["sha256"]))
        server = m.group(1).decode("utf-8", "replace").strip() if m else None
    return {
        "deposit": tester.get("deposit"), "currency": tester.get("currency"), "leverage": tester.get("leverage"),
        "model": tester.get("model"), "fixed_inputs": json.loads(rnd["fixed_json"]),
        "symbol": rnd["symbol"], "timeframe": rnd["timeframe"], "server": server,
        "account_margin_mode": None,
        "note": "values at the round's standard conditions; the account margin mode is not reported by the "
                "optimization outputs; margin safety at real lots is evaluated in P9 (C52)",
    }


def summarize(db: Database, filter_id: str) -> dict[str, Any]:
    fx = db.one("SELECT * FROM filter_execution WHERE filter_id = ?", (filter_id,))
    if fx is None:
        raise FilterError(f"unknown filter execution {filter_id}")
    reasons: dict[str, int] = {}
    warnings: dict[str, int] = {}
    for r in db.all("SELECT reason_codes_json, warning_codes_json FROM decision WHERE filter_id = ?", (filter_id,)):
        for c in json.loads(r["reason_codes_json"]):
            reasons[c] = reasons.get(c, 0) + 1
        for c in json.loads(r["warning_codes_json"]):
            warnings[c] = warnings.get(c, 0) + 1
    runs = db.one("SELECT COUNT(*) AS n FROM filter_execution WHERE round_id = ?", (fx["round_id"],))["n"]
    return {
        "filter_id": filter_id, "round_id": fx["round_id"], "profile": fx["profile_name"],
        "profile_hash": fx["profile_hash"][:16], "component": f"{fx['component_name']} {fx['component_version']}",
        "candidates": fx["n_candidates"], "passed": fx["n_pass"], "failed": fx["n_candidates"] - fx["n_pass"],
        "reason_counts": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "warning_counts": dict(sorted(warnings.items())),
        "filter_runs_on_this_round": runs,
        "conditions": json.loads(fx["conditions_json"]),
    }


def run_filter(workspace: Path, round_id: str, profiles_path: Path, profile_name: str) -> FilterOutcome:
    try:
        profile, profile_hash = load_profile(profiles_path, profile_name)
    except ConfigError as e:
        return FilterOutcome(EXIT_USER, "CONFIG_INVALID", str(e))
    workspace = workspace.resolve()
    db_path = workspace / "robustlab.sqlite"
    if not db_path.is_file():
        return FilterOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database at {db_path}")
    db = Database(db_path)
    try:
        store = ArtifactStore(workspace / "artifacts", db)
        try:
            rnd, job_ids = _round_inputs(db, round_id)
        except FilterError as e:
            return FilterOutcome(EXIT_USER, "ROUND_NOT_READY", str(e))
        filter_id = ids.content_id("fl", {"round_id": round_id, "profile_hash": profile_hash,
                                          "component": gates.FILTER_COMPONENT, "version": gates.FILTER_VERSION,
                                          "input_job_ids": job_ids})
        if db.one("SELECT 1 FROM filter_execution WHERE filter_id = ?", (filter_id,)):
            return FilterOutcome(EXIT_OK, "ALREADY_DONE", "this round was already filtered with the same profile "
                                 "and filter version", filter_id, summarize(db, filter_id))

        years = (dt.date.fromisoformat(rnd["to_date"]) - dt.date.fromisoformat(rnd["from_date"])).days / 365.25
        conditions = _conditions(db, store, rnd, job_ids)
        marks = ",".join("?" for _ in job_ids)
        rows = db.all(f"SELECT job_id, pass_no, run_id, candidate_id, metric_def_version, metrics_json "
                      f"FROM pass_result WHERE job_id IN ({marks}) ORDER BY job_id, pass_no", tuple(job_ids))
        now = now_iso()
        evaluations, decisions = [], []
        n_pass = 0
        for r in rows:
            metrics = json.loads(r["metrics_json"])
            d = gates.evaluate(metrics, profile, years)
            n_pass += d.outcome == gates.PASS
            input_hash = ids.sha256_hex(r["metrics_json"])
            evaluations.append({
                "evaluation_id": ids.content_id("ev", {"filter_id": filter_id, "candidate_id": r["candidate_id"]}),
                "candidate_id": r["candidate_id"], "stage": "FILTER", "evaluator_name": gates.FILTER_COMPONENT,
                "evaluator_version": gates.FILTER_VERSION, "config_hash": profile_hash,
                "input_run_ids_json": json.dumps([r["run_id"]]), "input_hash": input_hash,
                "values_json": json.dumps(d.values | {"metric_def_version": r["metric_def_version"],
                                                       "job_id": r["job_id"], "pass_no": r["pass_no"]},
                                          sort_keys=True),
                "filter_id": filter_id, "created_at": now,
            })
            decisions.append({
                "decision_id": ids.content_id("dc", {"filter_id": filter_id, "candidate_id": r["candidate_id"]}),
                "candidate_id": r["candidate_id"], "stage": "FILTER", "outcome": d.outcome,
                "component_name": gates.FILTER_COMPONENT, "component_version": gates.FILTER_VERSION,
                "reason_codes_json": json.dumps(d.reasons), "warning_codes_json": json.dumps(d.warnings),
                "details_json": json.dumps({"gates": d.gates_json(), "profile": profile_name,
                                            "profile_hash": profile_hash, "conditions": conditions,
                                            "run_id": r["run_id"]}, sort_keys=True, default=str),
                "filter_id": filter_id, "created_at": now,
            })
        with db.tx():
            for code, (cat, desc, sev) in gates.REASON_CODES.items():
                db.insert("reason_code", {"code": code, "category": cat, "description": desc, "severity": sev},
                          if_absent=True)
            db.insert("filter_execution", {
                "filter_id": filter_id, "round_id": round_id, "profile_name": profile_name,
                "profile_hash": profile_hash,
                "profile_json": ids.canonical_json(profile.model_dump(mode="json")),
                "component_name": gates.FILTER_COMPONENT, "component_version": gates.FILTER_VERSION,
                "input_job_ids_json": json.dumps(job_ids), "conditions_json": json.dumps(conditions, sort_keys=True),
                "n_candidates": len(rows), "n_pass": n_pass, "created_at": now,
            })
            for e in evaluations:
                db.insert("evaluation", e)
            for d in decisions:
                db.insert("decision", d)
        return FilterOutcome(EXIT_OK, "DONE", "", filter_id, summarize(db, filter_id))
    finally:
        db.close()


def why_not(db: Database, candidate_id: str, filter_id: str | None = None) -> dict[str, Any]:
    if filter_id is None:
        row = db.one("SELECT filter_id FROM decision WHERE candidate_id = ? AND stage = 'FILTER' "
                     "ORDER BY created_at DESC, filter_id DESC LIMIT 1", (candidate_id,))
        if row is None:
            raise FilterError(f"no filter decision for {candidate_id}")
        filter_id = row["filter_id"]
    d = db.one("SELECT * FROM decision WHERE candidate_id = ? AND filter_id = ?", (candidate_id, filter_id))
    if d is None:
        raise FilterError(f"no decision for {candidate_id} in {filter_id}")
    ev = db.one("SELECT values_json FROM evaluation WHERE candidate_id = ? AND filter_id = ?", (candidate_id, filter_id))
    cand = db.one("SELECT params_json FROM candidate WHERE candidate_id = ?", (candidate_id,))
    others = db.one("SELECT COUNT(*) AS n FROM decision WHERE candidate_id = ? AND stage = 'FILTER'", (candidate_id,))
    details = json.loads(d["details_json"])
    return {
        "candidate_id": candidate_id, "params": json.loads(cand["params_json"]) if cand else None,
        "filter_id": filter_id, "outcome": d["outcome"], "component": f"{d['component_name']} {d['component_version']}",
        "reasons": json.loads(d["reason_codes_json"]), "warnings": json.loads(d["warning_codes_json"]),
        "gates": details["gates"], "values": json.loads(ev["values_json"]) if ev else {},
        "profile": details["profile"], "profile_hash": details["profile_hash"][:16],
        "conditions": details["conditions"], "decisions_for_candidate": others["n"],
    }
