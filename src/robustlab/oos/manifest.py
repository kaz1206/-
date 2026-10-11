"""P8b OOS: lock a manifest, run the holdout once, burn it, decide (P8B_PLAN §1-§4, C74-C76).

- One manifest per workspace. It fixes the candidates (P8a passes of one validation plan), the NUMERIC lower
  edge of each prediction band (computed from the validation evidence only, fix1), the criteria, the holdout
  period (partition hash) and the extra cost. Never edited.
- `run` does nothing without the explicit confirmation. The holdout is BURNED before the first job starts
  (whatever the job's outcome). Afterwards only technical retries of the same manifest and job spec are allowed.
- Decisions are pass / fail against the pre-registered criteria only. A pass means "not refuted by the criteria",
  not proof of an edge; a fail means "did not meet the criteria", not proof that an edge disappeared (it can also
  come from the winner's curse of selecting on the validation data, P8B_PLAN §2.3-§2.4).
- Rehearsal workspaces (partition `rehearsal: true`) may lock a manifest for any evaluated P8a candidate; they never
  write decisions. Production workspaces refuse --rehearsal.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.config.loader import ConfigError, load_cost_stress, load_partition
from robustlab.core import ids
from robustlab.oos.holdout_guard import OosAccess
from robustlab.stats import validation as ev
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso
from robustlab.validation import run as vrun

COMPONENT = "oos"
VERSION = f"p8b1+{ev.BAND_VERSION}"
STAGE = "OOS"
CONFIRM_FLAG = "--i-understand-this-is-final"
EXIT_OK, EXIT_USER, EXIT_RUNTIME = 0, 1, 2
REASON_CODES: dict[str, tuple[str, str, str]] = {
    "OOS_BELOW_PREDICTION_BAND": ("oos", "OOS daily Sharpe below the pre-registered lower edge of the band", "FAIL"),
    "OOS_LOSS": ("oos", "OOS net profit <= 0", "FAIL"),
    "OOS_RUIN": ("bankruptcy", "bankruptcy or stop-out in the OOS", "FAIL"),
    "OOS_COST_FRAGILE": ("robustness", "OOS net profit <= 0 after the extra cost", "FAIL"),
    "HOLDOUT_CONTAMINATED": ("contamination", "the round was created after the holdout was burned", "FAIL"),
}
PASS_TEXT = ("PASS = not refuted by the criteria fixed before the run. It is NOT proof that an edge exists or "
             "will continue.")
FAIL_TEXT = ("FAIL = the criteria fixed before the run were not met. It is NOT proof that an edge disappeared "
             "(a real but weak edge can also lose money over a short OOS).")
BAND_FAIL_TEXT = (" The band gate failed: the band is centred on validation results of candidates selected on that same "
                  "data (winner's curse), so it is biased upwards.")
SIMULATION_NOTE = ("Simulation estimates, NOT error rates for this EA: under the simulated conditions (daily Sharpe "
                   "0.05-0.12, independent / AR(1) / GARCH returns, 631 validation days, 520 OOS days), a real edge "
                   "that persists fell below the band in 8-14% of P8a passes for moderate edges (5.6% for daily "
                   "Sharpe 0.12), and with the profit gate a weak edge (0.05) failed in about 14%; AR(2)-type "
                   "dependence is worse. See P8B_PLAN §2.2-§2.4.")
REUSE_WARNING = ("The holdout is burned for this workspace only. Do not use the same price period as a holdout in "
                 "another workspace: the platform cannot detect that.")


class OosError(ValueError):
    pass


@dataclass
class OosOutcome:
    exit_code: int
    status: str
    message: str = ""
    manifest_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def holdout_period(partition: Any) -> tuple[dt.date, dt.date]:
    """[holdout_from, holdout_to + 1 day): the partition's holdout dates are inclusive."""
    return partition.holdout_from, partition.holdout_to + dt.timedelta(days=1)


def weekdays(a: dt.date, b: dt.date) -> int:
    return int(np.busday_count(a.isoformat(), b.isoformat()))


def lock(workspace: Path, plan_id: str, partition_path: Path, cost_path: Path,
         rehearsal_candidate: str | None = None) -> OosOutcome:
    workspace = workspace.resolve()
    if not (workspace / "robustlab.sqlite").is_file():
        return OosOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database in {workspace}")
    try:
        partition, phash = load_partition(partition_path)
        cost, chash = load_cost_stress(cost_path)
    except ConfigError as e:
        return OosOutcome(EXIT_USER, "CONFIG_INVALID", str(e))
    if rehearsal_candidate and not partition.rehearsal:
        return OosOutcome(EXIT_USER, "INPUT_INVALID", "--rehearsal needs a rehearsal partition (rehearsal: true); "
                                                      "the production holdout is never used for a rehearsal")
    db = Database(workspace / "robustlab.sqlite")
    try:
        reg = db.one("SELECT partition_hash FROM partition_registry WHERE id = 1")
        if reg is None or reg["partition_hash"] != phash:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", "this partition is not the one pinned in the workspace")
        if db.one("SELECT 1 FROM holdout_state WHERE partition_hash = ? AND state = 'BURNED'", (phash,)):
            return OosOutcome(EXIT_USER, "HOLDOUT_BURNED", "the holdout of this workspace is already burned")
        existing = db.one("SELECT manifest_id FROM oos_manifest ORDER BY created_at LIMIT 1")
        if existing:
            return OosOutcome(EXIT_USER, "MANIFEST_EXISTS",
                              f"this workspace already has manifest {existing['manifest_id']} (one per workspace)")
        vp = db.one("SELECT * FROM validation_plan WHERE plan_id = ?", (plan_id,))
        if vp is None:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", f"unknown validation plan {plan_id}")
        evals = {r["candidate_id"]: json.loads(r["values_json"]) for r in db.all(
            "SELECT candidate_id, values_json FROM evaluation WHERE plan_id = ?", (plan_id,))}
        if rehearsal_candidate:
            if rehearsal_candidate not in evals:
                return OosOutcome(EXIT_USER, "INPUT_INVALID", f"{rehearsal_candidate} has no P8a result in {plan_id}")
            cands = [rehearsal_candidate]
        else:
            if vp["diagnostic"]:
                return OosOutcome(EXIT_USER, "INPUT_INVALID", "a diagnostic validation plan never feeds the OOS")
            cands = sorted(r["candidate_id"] for r in db.all(
                "SELECT candidate_id FROM decision WHERE plan_id = ? AND stage = 'VALIDATION' AND outcome = 'PASS'",
                (plan_id,)))
            if not cands:
                return OosOutcome(EXIT_USER, "NO_CANDIDATES", "no candidate passed this validation plan")
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (vp["round_id"],))
        if rnd["symbol"] not in cost.symbols:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", f"{rnd['symbol']} is not in {cost_path.name}")
        h_from, h_to = holdout_period(partition)
        n_oos = weekdays(h_from, h_to)
        bands = {cd: ev.oos_band(evals[cd]["evidence"], n_oos) for cd in cands}
        bad = [cd for cd, b in bands.items() if b["lower"] is None]
        if bad:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", f"no prediction band for {bad}: {bands[bad[0]]['reason']}")
        criteria = {"band": "OOS daily Sharpe >= lower edge (one-sided 95%, fix1)", "profit": "OOS net profit > 0",
                    "ruin": "no bankruptcy or stop-out", "cost": "OOS net profit after the extra cost > 0",
                    "p_value": "recorded only", "version": VERSION}
        manifest_id = ids.content_id("om", {"plan_id": plan_id, "candidates": cands, "bands": bands,
                                            "criteria": criteria, "partition_hash": phash, "cost_hash": chash,
                                            "rehearsal": bool(rehearsal_candidate)})
        db.insert("oos_manifest", {
            "manifest_id": manifest_id, "plan_id": plan_id, "round_id": rnd["round_id"],
            "strategy_family": rnd["strategy_family"], "candidates_json": json.dumps(cands),
            "bands_json": json.dumps(bands), "criteria_json": json.dumps(criteria), "partition_hash": phash,
            "holdout_from": h_from.isoformat(), "holdout_to": h_to.isoformat(),
            "cost_json": json.dumps(cost.model_dump(mode="json")), "cost_hash": chash,
            "rehearsal": int(bool(rehearsal_candidate)), "created_at": now_iso()})
        return OosOutcome(EXIT_OK, "LOCKED", REUSE_WARNING, manifest_id, summarize(db, manifest_id))
    finally:
        db.close()


def _done(db: Database, manifest_id: str) -> dict[str, Any]:
    out = {}
    for r in db.all("SELECT * FROM oos_run WHERE manifest_id = ? AND status = 'SUCCEEDED' ORDER BY attempt_no",
                    (manifest_id,)):
        out[r["candidate_id"]] = r
    return out


def run(workspace: Path, manifest_id: str, terminal_config: Path, partition_path: Path,
        confirmed: bool = False) -> OosOutcome:
    from robustlab.single_backtest import run_backtest

    workspace = workspace.resolve()
    if not confirmed:
        return OosOutcome(EXIT_USER, "NOT_CONFIRMED", f"nothing was run: the OOS runs once only. Add {CONFIRM_FLAG} "
                                                      "to run it and burn the holdout")
    try:
        partition, phash = load_partition(partition_path)
    except ConfigError as e:
        return OosOutcome(EXIT_USER, "CONFIG_INVALID", str(e))
    db = Database(workspace / "robustlab.sqlite")
    try:
        man = db.one("SELECT * FROM oos_manifest WHERE manifest_id = ?", (manifest_id,))
        if man is None:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", f"unknown manifest {manifest_id} (lock it first)")
        if man["partition_hash"] != phash:
            return OosOutcome(EXIT_USER, "INPUT_INVALID", "the partition differs from the one in the manifest")
        if db.one("SELECT 1 FROM evaluation WHERE manifest_id = ?", (manifest_id,)):
            return OosOutcome(EXIT_USER, "ALREADY_RUN", "the OOS of this manifest has already been run and decided; "
                                                        "it cannot be run again", manifest_id, summarize(db, manifest_id))
        burned = db.one("SELECT manifest_id FROM holdout_state WHERE partition_hash = ? AND state = 'BURNED'",
                        (phash,))
        if burned and burned["manifest_id"] != manifest_id:
            return OosOutcome(EXIT_USER, "HOLDOUT_BURNED", f"the holdout was burned by {burned['manifest_id']}")
        if burned is None:  # burn BEFORE the first job, whatever its outcome
            db.insert("holdout_state", {"partition_hash": phash, "state": "BURNED", "manifest_id": manifest_id,
                                        "changed_at": now_iso()})
        vp = db.one("SELECT * FROM validation_plan WHERE plan_id = ?", (man["plan_id"],))
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (man["round_id"],))
        cands = json.loads(man["candidates_json"])
        done = _done(db, manifest_id)
    finally:
        db.close()
    access = OosAccess(manifest_id, tuple(cands))
    reqdir = workspace / "oos" / manifest_id
    reqdir.mkdir(parents=True, exist_ok=True)
    problems = []
    for cd in cands:
        if cd in done:
            continue
        db = Database(workspace / "robustlab.sqlite")
        try:
            params = vrun._request_params(json.loads(db.one("SELECT params_json FROM candidate WHERE candidate_id = ?",
                                                            (cd,))["params_json"]), Path(vp["strategy_path"]))
            attempt = db.one("SELECT COUNT(*) AS n FROM oos_run WHERE manifest_id = ? AND candidate_id = ?",
                             (manifest_id, cd))["n"] + 1
        finally:
            db.close()
        req = {"strategy_file": vp["strategy_path"], "symbol": rnd["symbol"], "timeframe": rnd["timeframe"],
               "from_date": man["holdout_from"], "to_date": man["holdout_to"], "params": params,
               "tester": vrun._tester(rnd), "coverage_tolerance_days": vrun.COVERAGE_TOLERANCE_DAYS}
        path = reqdir / f"{cd}.yaml"
        path.write_text(yaml.safe_dump(req, sort_keys=True), encoding="utf-8")
        out = run_backtest(path, terminal_config, partition_path, workspace, oos=access)
        status = "SUCCEEDED" if out.status in ("SUCCEEDED", "ALREADY_SUCCEEDED") else out.status
        db = Database(workspace / "robustlab.sqlite")
        try:
            now = now_iso()
            db.insert("oos_run", {"manifest_id": manifest_id, "candidate_id": cd, "attempt_no": attempt,
                                  "status": status, "run_id": out.run_id, "job_id": out.job_id,
                                  "message": out.message, "created_at": now})
            if status == "SUCCEEDED":
                db.insert("period_exposure", {
                    "strategy_family": man["strategy_family"], "from_date": man["holdout_from"],
                    "to_date": man["holdout_to"], "source": "OOS_REHEARSAL" if man["rehearsal"] else "OOS",
                    "plan_id": man["plan_id"], "candidate_id": cd, "run_id": out.run_id, "seen_at": now,
                    "recorded_at": now})
        finally:
            db.close()
        if status != "SUCCEEDED":
            problems.append(f"{cd}: {out.status} {out.message}")
    db = Database(workspace / "robustlab.sqlite")
    try:
        if problems:
            return OosOutcome(EXIT_RUNTIME, "INCOMPLETE", "; ".join(problems) + " (the holdout stays burned; rerun "
                              f"`rlab oos run {manifest_id} {CONFIRM_FLAG}` to retry the same job spec)",
                              manifest_id, summarize(db, manifest_id))
        _evaluate(db, ArtifactStore(workspace / "artifacts", db), man, rnd)
        return OosOutcome(EXIT_OK, "DONE", "", manifest_id, summarize(db, manifest_id))
    finally:
        db.close()


def _seed(manifest_id: str, cd: str) -> int:
    return int(hashlib.sha256(f"{manifest_id}|{cd}".encode()).hexdigest()[:8], 16)


def _evaluate(db: Database, store: ArtifactStore, man: Any, rnd: Any) -> None:
    bands = json.loads(man["bands_json"])
    width = json.loads(man["cost_json"])["symbols"][rnd["symbol"]]["extra_price_per_round_trip"]
    done = _done(db, man["manifest_id"])
    now = now_iso()
    with db.tx():
        for code, (cat, desc, sev) in REASON_CODES.items():
            db.insert("reason_code", {"code": code, "category": cat, "description": desc, "severity": sev},
                      if_absent=True)
        for cd in json.loads(man["candidates_json"]):
            pv = vrun.period_values(db, store, done[cd]["job_id"], width)
            r = pv["returns"]
            sr = float(r.mean() / r.std(ddof=1)) if len(r) >= 2 and r.std(ddof=1) > 0 else None
            lower = bands[cd]["lower"]
            net = pv["net_profit"]
            net_cost = None if pv["extra_cost_1x"] is None else net - pv["extra_cost_1x"]
            rng = np.random.Generator(np.random.PCG64(_seed(man["manifest_id"], cd)))
            p_only = ev.evidence([r], vrun.BOOT_REPS, rng)
            gates = [
                {"gate": "band", "code": "OOS_BELOW_PREDICTION_BAND",
                 "status": "FAIL" if sr is None or sr < lower else "PASS", "value": sr, "threshold": lower},
                {"gate": "profit", "code": "OOS_LOSS", "status": "FAIL" if net <= 0 else "PASS", "value": net,
                 "threshold": "> 0"},
                {"gate": "ruin", "code": "OOS_RUIN", "status": "FAIL" if pv["bankrupt"] or pv["stop_out_deals"]
                 else "PASS", "value": {"bankrupt": pv["bankrupt"], "stop_out_deals": pv["stop_out_deals"]}},
                {"gate": "cost", "code": "OOS_COST_FRAGILE", "status": "FAIL" if net_cost is None or net_cost <= 0
                 else "PASS", "value": net_cost, "threshold": "> 0"},
            ]
            reasons = [g["code"] for g in gates if g["status"] == "FAIL"]
            values = {
                "sample_role": "OOS", "rehearsal": bool(man["rehearsal"]), "gates": gates, "oos_sharpe": sr,
                "band": bands[cd], "net_profit": net, "net_after_cost": net_cost, "days": pv["days"],
                "trades": pv["trades"], "observed_spread_points": pv["observed_spread_points"],
                "p_value_recorded_only": p_only["p"], "psr_recorded_only": p_only.get("psr"),
                "interpretation": PASS_TEXT if not reasons else
                FAIL_TEXT + (BAND_FAIL_TEXT if "OOS_BELOW_PREDICTION_BAND" in reasons else ""),
                "simulation_note": SIMULATION_NOTE,
                "version": VERSION,
            }
            db.insert("evaluation", {
                "evaluation_id": ids.content_id("ev", {"manifest_id": man["manifest_id"], "candidate_id": cd}),
                "candidate_id": cd, "stage": STAGE, "evaluator_name": COMPONENT, "evaluator_version": VERSION,
                "config_hash": ids.sha256_hex(man["criteria_json"]), "input_run_ids_json": json.dumps([done[cd]["run_id"]]),
                "input_hash": ids.sha256_hex(man["manifest_id"]), "values_json": json.dumps(values, default=vrun._jsonable),
                "filter_id": None, "manifest_id": man["manifest_id"], "created_at": now})
            if not man["rehearsal"]:
                db.insert("decision", {
                    "decision_id": ids.content_id("dc", {"manifest_id": man["manifest_id"], "candidate_id": cd}),
                    "candidate_id": cd, "stage": STAGE, "outcome": "FAIL" if reasons else "PASS",
                    "component_name": COMPONENT, "component_version": VERSION,
                    "reason_codes_json": json.dumps(reasons), "warning_codes_json": json.dumps([]),
                    "details_json": json.dumps({"manifest_id": man["manifest_id"], "gates": gates,
                                                "interpretation": values["interpretation"]}, default=vrun._jsonable),
                    "filter_id": None, "manifest_id": man["manifest_id"], "created_at": now})


def summarize(db: Database, manifest_id: str) -> dict[str, Any]:
    man = db.one("SELECT * FROM oos_manifest WHERE manifest_id = ?", (manifest_id,))
    if man is None:
        raise OosError(f"unknown manifest {manifest_id}")
    burned = db.one("SELECT * FROM holdout_state WHERE partition_hash = ? AND state = 'BURNED'",
                    (man["partition_hash"],))
    evs = {r["candidate_id"]: json.loads(r["values_json"]) for r in db.all(
        "SELECT candidate_id, values_json FROM evaluation WHERE manifest_id = ?", (manifest_id,))}
    decs = {r["candidate_id"]: r for r in db.all("SELECT * FROM decision WHERE manifest_id = ?", (manifest_id,))}
    bands = json.loads(man["bands_json"])
    results = []
    for cd in json.loads(man["candidates_json"]):
        item: dict[str, Any] = {"candidate_id": cd, "band_lower": bands[cd]["lower"], "band_center": bands[cd]["center"]}
        v = evs.get(cd)
        if v:
            item |= {"outcome": decs[cd]["outcome"] if cd in decs else "REHEARSAL (no decision)",
                     "reasons": [g["code"] for g in v["gates"] if g["status"] == "FAIL"], "oos_sharpe": v["oos_sharpe"],
                     "net_profit": v["net_profit"], "net_after_cost": v["net_after_cost"], "days": v["days"],
                     "trades": v["trades"], "p_value_recorded_only": v["p_value_recorded_only"],
                     "interpretation": v["interpretation"]}
        results.append(item)
    runs = [dict(r) for r in db.all("SELECT candidate_id, attempt_no, status, run_id FROM oos_run WHERE manifest_id = ? "
                                    "ORDER BY candidate_id, attempt_no", (manifest_id,))]
    return {"manifest_id": manifest_id, "plan_id": man["plan_id"], "round_id": man["round_id"],
            "rehearsal": bool(man["rehearsal"]), "holdout": f"[{man['holdout_from']}, {man['holdout_to']})",
            "holdout_state": "BURNED" if burned else "SEALED", "burned_at": burned["changed_at"] if burned else None,
            "candidates": json.loads(man["candidates_json"]), "criteria": json.loads(man["criteria_json"]),
            "locked_at": man["created_at"], "runs": runs, "results": results, "pass_text": PASS_TEXT,
            "fail_text": FAIL_TEXT, "simulation_note": SIMULATION_NOTE, "reuse_warning": REUSE_WARNING}


def log_access(db: Database, manifest_id: str, action: str) -> None:
    db.insert("oos_access_log", {"manifest_id": manifest_id, "action": action, "accessed_at": now_iso()})
