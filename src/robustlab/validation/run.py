"""P8a multi-period validation: lock a plan, run single tests with fixed parameters, decide (P8_PLAN §3-§6).

- A plan is locked (hashed, append-only) before any validation job. Parameters come from the candidate table;
  there is no way to change them. Periods may not touch the Discovery period or the holdout (+ embargo).
- Exposure ledger: every stored validation result records (strategy family, period). A period seen before the
  round was created is contaminated for that round's candidates (VALIDATION_CONTAMINATED).
- Every job attempt is recorded (validation_run); technical retries reuse the same job spec; every `validate show`
  is logged (validation_access_log).
- Decisions are pass / fail only, never a ranking (ARCHITECTURE §6.2 note 1). The evidence gate failing means
  INSUFFICIENT EVIDENCE, not "no edge". Diagnostic plans never write decisions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.config.loader import (
    ConfigError,
    _parse,
    load_cost_stress,
    load_partition,
    load_validation_plan,
    load_yaml,
)
from robustlab.core import ids
from robustlab.core.models import StrategySpec
from robustlab.metrics import store as metrics_store
from robustlab.mt5 import telemetry_reader as tr
from robustlab.oos.holdout_guard import check_overlap
from robustlab.stats import validation as ev
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso
from robustlab.validation import costs

COMPONENT = "validation"
VERSION = f"p8a1+{ev.EVIDENCE_VERSION}"
STAGE = "VALIDATION"
SAMPLE_ROLE = "VALIDATION"
DISCOVERY = "DISCOVERY"
BOOT_REPS = 2000
COVERAGE_TOLERANCE_DAYS = 7
PROFIT_TOLERANCE = 0.01
EXIT_OK, EXIT_USER, EXIT_RUNTIME = 0, 1, 2
INSUFFICIENT_EVIDENCE_TEXT = ("insufficient evidence: the validation periods are too short to tell this result "
                              "from chance. This does NOT mean the candidate has no edge.")
REASON_CODES: dict[str, tuple[str, str, str]] = {
    "REPRODUCIBILITY_MISMATCH": ("data", "the Discovery single test does not reproduce the optimization pass", "FAIL"),
    "VALIDATION_RUIN": ("bankruptcy", "bankruptcy or stop-out in a validation period", "FAIL"),
    "VALIDATION_PERIOD_LOSS": ("validation", "net profit over the validation periods <= 0", "FAIL"),
    "VALIDATION_INSUFFICIENT_EVIDENCE": ("validation", INSUFFICIENT_EVIDENCE_TEXT, "FAIL"),
    "COST_FRAGILE": ("robustness", "net profit over the validation periods <= 0 after the extra cost", "FAIL"),
    "VALIDATION_PERIOD_LOSS_SINGLE": ("validation", "a single validation period lost money (warning)", "WARN"),
    "PROFIT_CONCENTRATED": ("robustness", "the closing deals without the top 5 sum to <= 0 (warning)", "WARN"),
    "VALIDATION_REVISED": ("validation", "another plan was locked for this selection before", "WARN"),
    "PRIOR_EXPOSURE": ("validation", "a validation period had been seen before this plan was locked", "WARN"),
    "VALIDATION_CONTAMINATED": ("contamination", "the period was seen before the round was created", "FAIL"),
}


class ValidateError(ValueError):
    pass


@dataclass
class ValidateOutcome:
    exit_code: int
    status: str
    message: str = ""
    plan_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def _overlaps(a_from: str, a_to: str, b_from: str, b_to: str) -> bool:
    return a_from < b_to and b_from < a_to


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _exposures(db: Database, family: str, strategy_name: str, periods: list[dict[str, str]],
               discovery: tuple[str, str]) -> list[dict[str, Any]]:
    """Every earlier sight of the plan's periods: the ledger plus single tests run outside a plan."""
    out = []
    for p in periods:
        for r in db.all("SELECT * FROM period_exposure WHERE strategy_family = ?", (family,)):
            if _overlaps(p["from"], p["to"], r["from_date"], r["to_date"]):
                out.append({"period": p["name"], "source": r["source"], "seen_at": r["seen_at"],
                            "plan_id": r["plan_id"]})
        for r in db.all("SELECT r.run_id, r.from_date, r.to_date, j.finished_at FROM run r "
                        "JOIN candidate c USING (candidate_id) JOIN strategy_version s USING (strategy_version_id) "
                        "JOIN job j ON j.run_id = r.run_id AND j.status = 'SUCCEEDED' WHERE s.strategy_name = ?",
                        (strategy_name,)):
            if _overlaps(p["from"], p["to"], r["from_date"], r["to_date"]) and \
                    (r["from_date"], r["to_date"]) != discovery:
                out.append({"period": p["name"], "source": "SINGLE_TEST", "seen_at": r["finished_at"],
                            "run_id": r["run_id"]})
    return out


def lock(workspace: Path, sel_id: str, plan_path: Path, cost_path: Path, partition_path: Path,
         diagnostic_candidate: str | None = None) -> ValidateOutcome:
    workspace = workspace.resolve()
    if not (workspace / "robustlab.sqlite").is_file():
        return ValidateOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database in {workspace}")
    try:
        plan, strategy_path, plan_hash = load_validation_plan(plan_path)
        cost, cost_hash = load_cost_stress(cost_path)
        partition, _ = load_partition(partition_path)
        strategy = _parse(StrategySpec, strategy_path, load_yaml(strategy_path))
    except ConfigError as e:
        return ValidateOutcome(EXIT_USER, "CONFIG_INVALID", str(e))
    db = Database(workspace / "robustlab.sqlite")
    try:
        sx = db.one("SELECT * FROM selection_execution WHERE sel_id = ?", (sel_id,))
        if sx is None:
            return ValidateOutcome(EXIT_USER, "INPUT_INVALID", f"unknown selection execution {sel_id}")
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (sx["round_id"],))
        sv = db.one("SELECT * FROM strategy_version WHERE strategy_version_id = ?", (rnd["strategy_version_id"],))
        if strategy.strategy_name != sv["strategy_name"] or strategy.telemetry_version != sv["telemetry_version"]:
            return ValidateOutcome(EXIT_USER, "INPUT_INVALID", "the plan's strategy file is not the round's strategy")
        if rnd["symbol"] not in cost.symbols:
            return ValidateOutcome(EXIT_USER, "INPUT_INVALID",
                                   f"{rnd['symbol']} is not in {cost_path.name}: no default extra cost is used")
        if diagnostic_candidate:
            ok = db.one("SELECT 1 FROM pass_result p JOIN opt_job j USING (job_id) WHERE j.round_id = ? AND "
                        "p.candidate_id = ?", (rnd["round_id"], diagnostic_candidate))
            if ok is None:
                return ValidateOutcome(EXIT_USER, "INPUT_INVALID", f"{diagnostic_candidate} is not in the round")
            cands = [diagnostic_candidate]
        else:
            cands = json.loads(sx["selected_json"])
            if not cands:
                return ValidateOutcome(EXIT_USER, "NO_CANDIDATES",
                                       "the selection chose no candidate; use --diagnostic --candidate to check "
                                       "the machinery without a decision")
        emb = dt.timedelta(days=partition.embargo_days)
        d_from, d_to = _d(rnd["from_date"]), _d(rnd["to_date"])
        periods = [{"name": p.name, "from": p.from_date.isoformat(), "to": p.to_date.isoformat()}
                   for p in sorted(plan.periods, key=lambda p: p.from_date)]
        for p in plan.periods:
            if not (p.to_date + emb <= d_from or p.from_date >= d_to + emb):
                return ValidateOutcome(EXIT_USER, "INPUT_INVALID",
                                       f"period {p.name} touches the Discovery period {d_from}..{d_to} "
                                       f"or its {partition.embargo_days}-day embargo")
            g = check_overlap(p.from_date, p.to_date, partition)
            if not g.approved:
                return ValidateOutcome(3, "GUARD_REJECTED", f"period {p.name}: {g.reason}")
        exp = _exposures(db, rnd["strategy_family"], sv["strategy_name"], periods,
                         (rnd["from_date"], rnd["to_date"]))
        contaminated = [e for e in exp if e["seen_at"] and e["seen_at"] < rnd["created_at"]]
        if contaminated:
            return ValidateOutcome(EXIT_USER, "VALIDATION_CONTAMINATED",
                                   f"period {contaminated[0]['period']} was seen ({contaminated[0]['source']}, "
                                   f"{contaminated[0]['seen_at']}) before round {rnd['round_id']} was created")
        constants = {"alpha": ev.ALPHA, "boot_reps": BOOT_REPS, "min_days": ev.MIN_DAYS, "version": VERSION,
                     "embargo_days": partition.embargo_days}
        plan_id = ids.content_id("vp", {"sel_id": sel_id, "plan_hash": plan_hash, "cost_hash": cost_hash,
                                        "candidates": cands, "diagnostic": bool(diagnostic_candidate),
                                        "constants": constants})
        if db.one("SELECT 1 FROM validation_plan WHERE plan_id = ?", (plan_id,)):
            return ValidateOutcome(EXIT_OK, "ALREADY_LOCKED", "this plan is already locked", plan_id,
                                   summarize(db, plan_id))
        revision_no = db.one("SELECT COUNT(*) AS n FROM validation_plan WHERE sel_id = ? AND diagnostic = ?",
                             (sel_id, int(bool(diagnostic_candidate))))["n"]
        db.insert("validation_plan", {
            "plan_id": plan_id, "sel_id": sel_id, "round_id": rnd["round_id"],
            "strategy_family": rnd["strategy_family"], "label": plan.label,
            "plan_json": json.dumps({"periods": periods, "strategy_file": plan.strategy_file}),
            "plan_hash": plan_hash, "strategy_path": str(strategy_path),
            "cost_json": json.dumps(cost.model_dump(mode="json")), "cost_hash": cost_hash,
            "constants_json": json.dumps(constants), "candidates_json": json.dumps(cands),
            "diagnostic": int(bool(diagnostic_candidate)), "revision_no": revision_no,
            "exposure_json": json.dumps(exp), "created_at": now_iso(),
        })
        return ValidateOutcome(EXIT_OK, "LOCKED", "", plan_id, summarize(db, plan_id))
    finally:
        db.close()


def _tester(rnd: Any) -> dict[str, Any]:
    t = json.loads(rnd["tester_settings_json"])
    return {k: v for k, v in t.items() if k not in ("terminal_path", "optimization")}


def _request_params(normalized: dict[str, Any], strategy_path: Path) -> dict[str, Any]:
    """The candidate's stored (normalized) values as request values; doubles are stored as text (P1 C21)."""
    spec = _parse(StrategySpec, strategy_path, load_yaml(strategy_path))
    types = {i.name: i.type.value for i in spec.inputs}
    return {k: (float(v) if types.get(k) == "double" and isinstance(v, str) else v) for k, v in normalized.items()}


def _jobs(db: Database, plan_id: str) -> dict[tuple[str, str], Any]:
    out = {}
    for r in db.all("SELECT * FROM validation_run WHERE plan_id = ? AND status IN ('SUCCEEDED', 'ALREADY_SUCCEEDED') "
                    "ORDER BY attempt_no", (plan_id,)):
        out[(r["candidate_id"], r["period_name"])] = r
    return out


def run(workspace: Path, plan_id: str, terminal_config: Path, partition_path: Path) -> ValidateOutcome:
    from robustlab.single_backtest import run_backtest

    workspace = workspace.resolve()
    db = Database(workspace / "robustlab.sqlite")
    try:
        vp = db.one("SELECT * FROM validation_plan WHERE plan_id = ?", (plan_id,))
        if vp is None:
            return ValidateOutcome(EXIT_USER, "INPUT_INVALID", f"unknown validation plan {plan_id} (lock it first)")
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (vp["round_id"],))
        plan = json.loads(vp["plan_json"])
        cands = json.loads(vp["candidates_json"])
        todo = [(cd, {"name": DISCOVERY, "from": rnd["from_date"], "to": rnd["to_date"]}) for cd in cands]
        todo += [(cd, p) for cd in cands for p in plan["periods"]]
        done = _jobs(db, plan_id)
    finally:
        db.close()
    reqdir = workspace / "validation" / plan_id
    reqdir.mkdir(parents=True, exist_ok=True)
    problems = []
    for cd, p in todo:
        if (cd, p["name"]) in done:
            continue
        db = Database(workspace / "robustlab.sqlite")
        try:
            params = _request_params(json.loads(db.one("SELECT params_json FROM candidate WHERE candidate_id = ?",
                                                       (cd,))["params_json"]), Path(vp["strategy_path"]))
            attempt = db.one("SELECT COUNT(*) AS n FROM validation_run WHERE plan_id = ? AND candidate_id = ? AND "
                             "period_name = ?", (plan_id, cd, p["name"]))["n"] + 1
        finally:
            db.close()
        req = {"strategy_file": vp["strategy_path"], "symbol": rnd["symbol"], "timeframe": rnd["timeframe"],
               "from_date": p["from"], "to_date": p["to"], "params": params, "tester": _tester(rnd),
               "coverage_tolerance_days": COVERAGE_TOLERANCE_DAYS}
        path = reqdir / f"{cd}_{p['name']}.yaml"
        path.write_text(yaml.safe_dump(req, sort_keys=True), encoding="utf-8")
        out = run_backtest(path, terminal_config, partition_path, workspace)
        db = Database(workspace / "robustlab.sqlite")
        try:
            job = db.one("SELECT created_at FROM job WHERE job_id = ?", (out.job_id,)) if out.job_id else None
            run_row = db.one("SELECT candidate_id FROM run WHERE run_id = ?", (out.run_id,)) if out.run_id else None
            status = out.status
            msg = out.message
            if run_row is not None and run_row["candidate_id"] != cd:
                status, msg = "STRATEGY_CHANGED", (f"the single test registered {run_row['candidate_id']}, not {cd}: "
                                                   "the EA or its inputs changed since the round")
            now = now_iso()
            db.insert("validation_run", {
                "plan_id": plan_id, "candidate_id": cd, "period_name": p["name"], "from_date": p["from"],
                "to_date": p["to"], "attempt_no": attempt, "status": status, "run_id": out.run_id,
                "job_id": out.job_id, "reused": int(out.status == "ALREADY_SUCCEEDED"),
                "job_created_at": job["created_at"] if job else None, "message": msg, "created_at": now})
            if status in ("SUCCEEDED", "ALREADY_SUCCEEDED") and p["name"] != DISCOVERY:
                db.insert("period_exposure", {
                    "strategy_family": vp["strategy_family"], "from_date": p["from"], "to_date": p["to"],
                    "source": "DIAGNOSTIC" if vp["diagnostic"] else "VALIDATION_RUN", "plan_id": plan_id,
                    "candidate_id": cd, "run_id": out.run_id, "seen_at": now, "recorded_at": now})
        finally:
            db.close()
        if status not in ("SUCCEEDED", "ALREADY_SUCCEEDED"):
            problems.append(f"{cd} {p['name']}: {status} {msg}")
            if status in ("STRATEGY_CHANGED", "GUARD_REJECTED", "CONFIG_INVALID", "PREFLIGHT_FAILED"):
                break
    db = Database(workspace / "robustlab.sqlite")
    try:
        if problems:
            return ValidateOutcome(EXIT_RUNTIME, "INCOMPLETE", "; ".join(problems) +
                                   " (rerun `rlab validate run` to retry technical failures with the same spec)",
                                   plan_id, summarize(db, plan_id))
        if db.one("SELECT 1 FROM evaluation WHERE plan_id = ?", (plan_id,)):
            return ValidateOutcome(EXIT_OK, "ALREADY_EVALUATED", "", plan_id, summarize(db, plan_id))
        _evaluate(db, ArtifactStore(workspace / "artifacts", db), vp, rnd)
        return ValidateOutcome(EXIT_OK, "DONE", "", plan_id, summarize(db, plan_id))
    finally:
        db.close()


def _artifact(db: Database, store: ArtifactStore, job_id: str, role: str) -> str | None:
    row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = ?", (job_id, role))
    return store.read(row["sha256"]).decode("utf-8-sig") if row else None


def period_values(db: Database, store: ArtifactStore, job_id: str, width: float) -> dict[str, Any]:
    """Returns, P&L, ruin and costs of one single test (its own deposit, no link to other periods)."""
    sm = metrics_store.load(db, job_id) or metrics_store.compute_and_store(db, store, job_id)
    env = json.loads(_artifact(db, store, job_id, "telemetry_env"))
    deals = tr.parse_csv_text(_artifact(db, store, job_id, "telemetry_deals"), tr.DEALS_COLUMNS, "deals")
    daily_text = _artifact(db, store, job_id, "telemetry_daily")
    daily = tr.parse_csv_text(daily_text, tr.DAILY_COLUMNS, "daily") if daily_text else []
    dep = float(env["initial_balance"])
    eq = np.array([float(r["equity_close"]) for r in daily])
    rets = np.diff(np.concatenate([[dep], eq])) / dep if len(eq) else np.array([])
    sym = env.get("symbol", {})
    ts, tv = float(sym.get("tick_size") or 0), float(sym.get("tick_value") or 0)
    m = sm.metrics
    so = int((env.get("tracking") or {}).get("stop_out_deals") or 0)
    return {"job_id": job_id, "deposit": dep, "days": len(rets), "returns": rets, "deals": deals,
            "net_profit": float(m.get("net_profit") or 0.0), "trades": int(m.get("trades") or 0),
            "bankrupt": bool(m.get("bankrupt")), "stop_out_deals": so,
            "extra_cost_1x": costs.extra_cost(deals, width, ts, tv, 1.0) if ts > 0 else None,
            "extra_cost_05x": costs.extra_cost(deals, width, ts, tv, 0.5) if ts > 0 else None,
            "tick_size": ts, "tick_value": tv, "observed_spread_points": (env.get("observed") or {}).get("spread"),
            "point": sym.get("point"), "profit_currency": sym.get("currency_profit"),
            "account_currency": env.get("account_currency")}


def _seed(plan_id: str, cd: str) -> int:
    return int(hashlib.sha256(f"{plan_id}|{cd}".encode()).hexdigest()[:8], 16)


def _evaluate(db: Database, store: ArtifactStore, vp: Any, rnd: Any) -> None:
    plan = json.loads(vp["plan_json"])
    cands = json.loads(vp["candidates_json"])
    width = json.loads(vp["cost_json"])["symbols"][rnd["symbol"]]["extra_price_per_round_trip"]
    jobs = _jobs(db, vp["plan_id"])
    sel = db.one("SELECT revision_no FROM selection_execution WHERE sel_id = ?", (vp["sel_id"],))
    per: dict[str, dict[str, Any]] = {}
    pvals: dict[str, float] = {}
    for cd in cands:
        disc = period_values(db, store, jobs[(cd, DISCOVERY)]["job_id"], width)
        pr = db.one("SELECT p.metrics_json FROM pass_result p JOIN opt_job j USING (job_id) WHERE j.round_id = ? AND "
                    "p.candidate_id = ? AND j.status = 'SUCCEEDED' ORDER BY j.created_at DESC LIMIT 1",
                    (rnd["round_id"], cd))
        pm = json.loads(pr["metrics_json"]) if pr else {}
        repro = {"single_net_profit": disc["net_profit"], "single_trades": disc["trades"],
                 "pass_net_profit": pm.get("net_profit"), "pass_trades": pm.get("trades")}
        repro["match"] = (pm.get("net_profit") is not None and abs(disc["net_profit"] - float(pm["net_profit"]))
                          <= PROFIT_TOLERANCE and disc["trades"] == pm.get("trades"))
        pv = {p["name"]: period_values(db, store, jobs[(cd, p["name"])]["job_id"], width) for p in plan["periods"]}
        rng = np.random.Generator(np.random.PCG64(_seed(vp["plan_id"], cd)))
        e = ev.evidence([pv[n]["returns"] for n in pv], BOOT_REPS, rng)
        pvals[cd] = e["p"]
        per[cd] = {"repro": repro, "periods": pv, "evidence": e}
    # Holm family: every candidate of every non-diagnostic plan of this selection locked so far (P8_PLAN §4.2)
    if vp["diagnostic"]:
        family = dict(pvals)
    else:
        family = {}
        for r in db.all("SELECT candidates_json FROM validation_plan WHERE sel_id = ? AND diagnostic = 0 AND "
                        "created_at <= ?", (vp["sel_id"], vp["created_at"])):
            for cd in json.loads(r["candidates_json"]):
                family.setdefault(cd, 1.0)  # no result in this plan -> p = 1 (conservative)
        family.update(pvals)
    rejected = ev.holm(family)
    reused_prior = {(r["candidate_id"], r["period_name"]) for r in db.all(
        "SELECT * FROM validation_run WHERE plan_id = ? AND reused = 1 AND job_created_at < ?",
        (vp["plan_id"], vp["created_at"])) if r["period_name"] != DISCOVERY}
    now = now_iso()
    with db.tx():
        for code, (cat, desc, sev) in REASON_CODES.items():
            db.insert("reason_code", {"code": code, "category": cat, "description": desc, "severity": sev},
                      if_absent=True)
        for cd in cands:
            x = per[cd]
            pv = x["periods"]
            net = sum(v["net_profit"] for v in pv.values())
            c1 = [v["extra_cost_1x"] for v in pv.values()]
            c05 = [v["extra_cost_05x"] for v in pv.values()]
            net_1x = None if None in c1 else net - sum(c1)
            net_05x = None if None in c05 else net - sum(c05)
            conc = costs.concentration([d for v in pv.values() for d in v["deals"]])
            gates = [
                {"gate": "reproducibility", "code": "REPRODUCIBILITY_MISMATCH",
                 "status": "PASS" if x["repro"]["match"] else "FAIL", "value": x["repro"]},
                {"gate": "ruin", "code": "VALIDATION_RUIN",
                 "status": "FAIL" if any(v["bankrupt"] or v["stop_out_deals"] for v in pv.values()) else "PASS",
                 "value": {n: {"bankrupt": v["bankrupt"], "stop_out_deals": v["stop_out_deals"]} for n, v in pv.items()}},
                {"gate": "pooled_profit", "code": "VALIDATION_PERIOD_LOSS", "status": "FAIL" if net <= 0 else "PASS",
                 "value": net, "threshold": "> 0"},
                {"gate": "evidence", "code": "VALIDATION_INSUFFICIENT_EVIDENCE",
                 "status": "PASS" if rejected[cd] else "FAIL", "value": x["evidence"]["p"],
                 "threshold": f"Holm, alpha {ev.ALPHA} one-sided, K = {len(family)}"},
                {"gate": "cost", "code": "COST_FRAGILE",
                 "status": "FAIL" if net_1x is None or net_1x <= 0 else "PASS", "value": net_1x, "threshold": "> 0",
                 "note": "no tick size/value in the telemetry" if net_1x is None else ""},
            ]
            reasons = [g["code"] for g in gates if g["status"] == "FAIL"]
            warnings = []
            if any(v["net_profit"] <= 0 for v in pv.values()):
                warnings.append("VALIDATION_PERIOD_LOSS_SINGLE")
            if conc["rest_sum"] <= 0 and conc["closing_deals"] > 0:
                warnings.append("PROFIT_CONCENTRATED")
            if x["evidence"].get("serial_dependence_high"):
                warnings.append("SERIAL_DEPENDENCE_HIGH")
            if vp["revision_no"]:
                warnings.append("VALIDATION_REVISED")
            if sel and sel["revision_no"]:
                warnings.append("SELECTION_REVISED")
            if any(k[0] == cd for k in reused_prior):
                warnings.append("PRIOR_EXPOSURE")
            values = {
                "sample_role": SAMPLE_ROLE, "diagnostic": bool(vp["diagnostic"]), "gates": gates,
                "periods": {n: {k: v[k] for k in ("job_id", "days", "net_profit", "trades", "bankrupt", "stop_out_deals",
                                                  "extra_cost_1x", "extra_cost_05x", "tick_size", "tick_value",
                                                  "observed_spread_points", "point", "profit_currency",
                                                  "account_currency")}
                            | {"sharpe": (float(v["returns"].mean() / v["returns"].std(ddof=1))
                                          if v["days"] >= 2 and v["returns"].std(ddof=1) > 0 else None)}
                            for n, v in pv.items()},
                "pooled": {"net_profit": net, "net_after_cost_1x": net_1x, "net_after_cost_05x": net_05x,
                           "extra_cost_width": width, "concentration": conc},
                "evidence": {k: v for k, v in x["evidence"].items()}, "holm": {"family": family, "alpha": ev.ALPHA,
                                                                                 "rejected": rejected[cd]},
                "reproducibility": x["repro"], "known_limitation": ev.KNOWN_LIMITATION,
                "insufficient_evidence_text": INSUFFICIENT_EVIDENCE_TEXT, "version": VERSION,
                "cost_note": ("extra cost = width / tick_size * tick_value * volume per closing deal, on top of the "
                              "tester result (spread, commission, swap and fee already included); tick_value at "
                              "the end of the test (approximate if the profit currency differs)"),
            }
            run_ids = [r["run_id"] for r in db.all("SELECT run_id FROM validation_run WHERE plan_id = ? AND "
                                                   "candidate_id = ? AND run_id IS NOT NULL", (vp["plan_id"], cd))]
            db.insert("evaluation", {
                "evaluation_id": ids.content_id("ev", {"plan_id": vp["plan_id"], "candidate_id": cd}),
                "candidate_id": cd, "stage": STAGE, "evaluator_name": COMPONENT, "evaluator_version": VERSION,
                "config_hash": vp["plan_hash"], "input_run_ids_json": json.dumps(sorted(set(run_ids))),
                "input_hash": ids.sha256_hex(vp["plan_id"]), "values_json": json.dumps(values, sort_keys=True,
                                                                                       default=_jsonable),
                "filter_id": None, "plan_id": vp["plan_id"], "created_at": now})
            if not vp["diagnostic"]:
                db.insert("decision", {
                    "decision_id": ids.content_id("dc", {"plan_id": vp["plan_id"], "candidate_id": cd}),
                    "candidate_id": cd, "stage": STAGE, "outcome": "FAIL" if reasons else "PASS",
                    "component_name": COMPONENT, "component_version": VERSION,
                    "reason_codes_json": json.dumps(reasons), "warning_codes_json": json.dumps(warnings),
                    "details_json": json.dumps({"plan_id": vp["plan_id"], "gates": gates}, default=_jsonable),
                    "filter_id": None, "plan_id": vp["plan_id"], "created_at": now})


def _jsonable(o: Any) -> Any:
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return str(o)


def summarize(db: Database, plan_id: str) -> dict[str, Any]:
    vp = db.one("SELECT * FROM validation_plan WHERE plan_id = ?", (plan_id,))
    if vp is None:
        raise ValidateError(f"unknown validation plan {plan_id}")
    runs = [dict(r) for r in db.all("SELECT candidate_id, period_name, attempt_no, status, run_id, job_id, reused "
                                    "FROM validation_run WHERE plan_id = ? ORDER BY candidate_id, period_name, "
                                    "attempt_no", (plan_id,))]
    evs = {r["candidate_id"]: json.loads(r["values_json"]) for r in db.all(
        "SELECT candidate_id, values_json FROM evaluation WHERE plan_id = ?", (plan_id,))}
    decs = {r["candidate_id"]: dict(r) for r in db.all("SELECT * FROM decision WHERE plan_id = ?", (plan_id,))}
    plan = json.loads(vp["plan_json"])
    exposure_counts = {}
    for p in plan["periods"]:
        n = db.one("SELECT COUNT(DISTINCT plan_id) AS n FROM period_exposure WHERE strategy_family = ? AND "
                   "from_date < ? AND ? < to_date", (vp["strategy_family"], p["to"], p["from"]))["n"]
        exposure_counts[p["name"]] = n
    results = []
    for cd in json.loads(vp["candidates_json"]):
        v = evs.get(cd)
        item: dict[str, Any] = {"candidate_id": cd}
        if v:
            item |= {"outcome": decs[cd]["outcome"] if cd in decs else "DIAGNOSTIC (no decision)",
                     "reasons": json.loads(decs[cd]["reason_codes_json"]) if cd in decs else
                     [g["code"] for g in v["gates"] if g["status"] == "FAIL"],
                     "p_value": v["evidence"]["p"], "psr": v["evidence"].get("psr"), "n_days": v["evidence"]["n"],
                     "pooled_net_profit": v["pooled"]["net_profit"],
                     "net_after_cost_1x": v["pooled"]["net_after_cost_1x"],
                     "periods": {n: {"net_profit": x["net_profit"], "trades": x["trades"], "days": x["days"],
                                     "sharpe": x["sharpe"], "observed_spread_points": x["observed_spread_points"]}
                                 for n, x in v["periods"].items()},
                     "reproducibility": v["reproducibility"]}
        results.append(item)
    return {"plan_id": plan_id, "sel_id": vp["sel_id"], "round_id": vp["round_id"], "label": vp["label"],
            "diagnostic": bool(vp["diagnostic"]), "revision_no": vp["revision_no"], "periods": plan["periods"],
            "candidates": json.loads(vp["candidates_json"]), "constants": json.loads(vp["constants_json"]),
            "cost": json.loads(vp["cost_json"]), "locked_at": vp["created_at"],
            "exposure_before_lock": json.loads(vp["exposure_json"]), "plans_that_saw_each_period": exposure_counts,
            "runs": runs, "results": results, "known_limitation": ev.KNOWN_LIMITATION,
            "insufficient_evidence_text": INSUFFICIENT_EVIDENCE_TEXT}


def log_access(db: Database, plan_id: str, candidate: str | None, action: str) -> None:
    db.insert("validation_access_log", {"plan_id": plan_id, "candidate_id": candidate, "action": action,
                                        "accessed_at": now_iso()})
