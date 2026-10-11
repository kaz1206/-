"""Statistical evaluation of a filtered round: DSR for every candidate, block-bootstrap intervals for the
selected ones (P6_PLAN §2-§5, C61-C66).

- Inputs: the Discovery-period pass metrics and daily Parquet of the jobs the P4 filter execution used, the P4
  decisions, the P5 region medoids, and (for N and V) every optimization round of the same strategy family.
- N and V use the FAMILY scope (C61): N = distinct requested configurations (each round's grid expanded,
  candidate_id de-duplicated, failed passes included); V = sample variance of the daily Sharpe over distinct
  (configuration, period) results whose score status is OK. The round scope is shown as a sensitivity.
- Every value is labelled DISCOVERY_IN_SAMPLE with its period. Nothing here decides pass/fail: DSR is for
  warnings and ranking only (C62, approval condition 1). Known limitations are stored with every execution.
- Idempotent per (round, filter, targets, reps, seed, settings, family inputs). Append-only.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from robustlab.core import ids
from robustlab.robustness import grid_stats
from robustlab.stats import bootstrap, dsr
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso

COMPONENT = "statistics"
VERSION = f"{dsr.DSR_VERSION}+{bootstrap.BOOTSTRAP_VERSION}"
SAMPLE_ROLE = "DISCOVERY_IN_SAMPLE"
STAGE = "STATISTICS"
DSR_WARN_BELOW = 0.95
CONFIG = {"n_scope": "family_distinct_requested_configurations", "v_scope": "family_distinct_config_period_ok",
          "sharpe": "daily, ddof=1, not annualized (C53)", "corrections": ["ar1", "newey_west", "bootstrap_se"],
          "conservative": "min of computed variants", "dsr_usage": dsr.USAGE, "dsr_warn_below": DSR_WARN_BELOW,
          "bootstrap": {"main": "stationary", "sensitivity": "circular", "block_length": "politis_white",
                        "interval": "percentile", "size": bootstrap.CI_SIZE, "dd_quantiles": bootstrap.DD_QUANTILES,
                        "min_days": bootstrap.MIN_DAYS},
          "serial_dependence_ratio": dsr.SERIAL_DEPENDENCE_RATIO}
NOTE = ("DISCOVERY_IN_SAMPLE: values come from the period the candidates were selected in. DSR corrects for "
        "selection among the trials; it is not out-of-sample evidence. Intervals are the sampling error of this "
        "period, not forecasts. DSR is used for warnings and ranking only, never as a pass/fail on its own (C62).")
EXCLUSION_REASON = {
    "BANKRUPTCY": "bankrupt/stop-out: the daily series after the event is not a return distribution",
    "STOP_OUT": "bankrupt/stop-out: the daily series after the event is not a return distribution",
    "NO_TRADES": "zero variance (no trades)",
    "NOT_COMPUTABLE": "daily Sharpe not computable",
}
EXIT_OK, EXIT_USER = 0, 1


class StatError(ValueError):
    pass


@dataclass
class StatOutcome:
    exit_code: int
    status: str
    message: str = ""
    stat_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def default_seed(round_id: str, filter_id: str) -> int:
    return int(hashlib.sha256(f"{round_id}|{filter_id}".encode()).hexdigest()[:8], 16)


def candidate_seed(seed: int, candidate_id: str) -> int:
    """Per-candidate seed: results do not depend on which other candidates are bootstrapped."""
    return int(hashlib.sha256(f"{seed}|{candidate_id}".encode()).hexdigest()[:8], 16)


def expand_round(rnd: Any) -> set[str]:
    """Candidate ids of every configuration the round requested (same construction as P3)."""
    space = json.loads(rnd["param_space_json"])
    fixed = json.loads(rnd["fixed_json"])
    out = set()
    for combo in itertools.product(*(a["values"] for a in space)):
        params = dict(fixed)
        params.update({a["name"]: v for a, v in zip(space, combo, strict=True)})
        out.add(ids.candidate_id(rnd["strategy_version_id"], rnd["symbol"], rnd["timeframe"], dict(sorted(params.items()))))
    return out


def _variance(values: list[float]) -> float | None:
    return float(np.var(values, ddof=1)) if len(values) >= 2 else None


def family_scope(db: Database, rnd: Any, round_job_ids: list[str]) -> dict[str, Any]:
    """N and V over the strategy family (C61), and the same over this round (sensitivity)."""
    rounds = db.all("SELECT * FROM optimization_round WHERE strategy_family = ? ORDER BY created_at, round_id",
                    (rnd["strategy_family"],))
    requested: set[str] = set()
    inputs = []
    for r in rounds:
        expanded = expand_round(r)
        requested |= expanded
        jobs = [j["job_id"] for j in db.all("SELECT job_id FROM opt_job WHERE round_id = ? AND status = 'SUCCEEDED' "
                                            "ORDER BY created_at, job_id", (r["round_id"],))]
        inputs.append({"round_id": r["round_id"], "from": r["from_date"], "to": r["to_date"],
                       "expanded": len(expanded), "succeeded_jobs": jobs})
    family: dict[tuple[str, str, str], tuple[str, float | None]] = {}
    for inp in inputs:
        if not inp["succeeded_jobs"]:
            continue
        marks = ",".join("?" for _ in inp["succeeded_jobs"])
        for row in db.all(f"SELECT p.candidate_id, p.metrics_json, r.from_date, r.to_date FROM pass_result p "
                          f"JOIN run r USING (run_id) WHERE p.job_id IN ({marks})", tuple(inp["succeeded_jobs"])):
            key = (row["candidate_id"], row["from_date"], row["to_date"])
            if key in family:
                continue  # rerun or overlapping round: the same configuration and period counts once
            s, st, _ = grid_stats.score_of(json.loads(row["metrics_json"]))
            family[key] = (st, s if st == "OK" else None)
    marks = ",".join("?" for _ in round_job_ids)
    round_vals: dict[str, tuple[str, float | None]] = {}
    for row in db.all(f"SELECT candidate_id, metrics_json FROM pass_result WHERE job_id IN ({marks})",
                      tuple(round_job_ids)):
        if row["candidate_id"] not in round_vals:
            s, st, _ = grid_stats.score_of(json.loads(row["metrics_json"]))
            round_vals[row["candidate_id"]] = (st, s if st == "OK" else None)
    fam_ok = [v for _, v in family.values() if v is not None]
    rnd_ok = [v for _, v in round_vals.values() if v is not None]
    ledger = db.one("SELECT COALESCE(SUM(n_trials_raw), 0) AS n FROM trial_ledger WHERE strategy_family = ?",
                    (rnd["strategy_family"],))["n"]
    return {
        "n_trials": len(requested), "n_trials_round": len(expand_round(rnd)), "trial_ledger_sum": int(ledger),
        "var_sr": _variance(fam_ok), "var_sr_count": len(fam_ok),
        "excluded": dict(sorted(Counter(st for st, v in family.values() if v is None).items())),
        "var_sr_round": _variance(rnd_ok), "var_sr_round_count": len(rnd_ok),
        "excluded_round": dict(sorted(Counter(st for st, v in round_vals.values() if v is None).items())),
        "rounds": inputs, "distinct_config_periods": len(family),
        "periods": sorted({(k[1], k[2]) for k in family}),
    }


def _daily_returns(db: Database, store: ArtifactStore, rnd: Any, job_ids: list[str],
                   deposit: float) -> dict[tuple, tuple[list[str], np.ndarray]]:
    names = [a["name"] for a in json.loads(rnd["param_space_json"])]
    acc: dict[tuple, list[tuple[str, float]]] = {}
    for jid in job_ids:
        art = db.one("SELECT sha256 FROM opt_job_artifact WHERE job_id = ? AND role = 'daily_parquet'", (jid,))
        if art is None:
            raise StatError(f"job {jid} has no daily Parquet")
        t = pq.read_table(io.BytesIO(store.read(art["sha256"]))).to_pydict()
        for k in range(len(t["date"])):
            acc.setdefault(tuple(float(t[n][k]) for n in names), []).append((t["date"][k], float(t["equity_close"][k])))
    out = {}
    for key, rows in acc.items():
        rows.sort()
        eq = np.array([e for _, e in rows])
        out[key] = ([d for d, _ in rows], np.diff(np.concatenate([[deposit], eq])) / deposit)  # C53
    return out


def _num(m: dict[str, Any], k: str) -> float | None:
    v = m.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def summarize(db: Database, stat_id: str) -> dict[str, Any]:
    sx = db.one("SELECT * FROM statistics_execution WHERE stat_id = ?", (stat_id,))
    if sx is None:
        raise StatError(f"unknown statistics execution {stat_id}")
    rows = [(r["candidate_id"], json.loads(r["values_json"])) for r in db.all(
        "SELECT candidate_id, values_json FROM evaluation WHERE stat_id = ?", (stat_id,))]
    cons = [v["dsr"]["dsr_conservative"] for _, v in rows if v["dsr"].get("dsr_conservative") is not None]
    std = [v["dsr"]["dsr"] for _, v in rows if v["dsr"].get("dsr") is not None]

    def q(xs: list[float]) -> dict[str, Any]:
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "max": round(max(xs), 6), "median": round(float(np.median(xs)), 6),
                ">=0.95": sum(1 for x in xs if x >= DSR_WARN_BELOW)}

    top = sorted(((v["dsr"]["dsr_conservative"], cd, v) for cd, v in rows if v["dsr"].get("dsr_conservative") is not None),
                 key=lambda t: (-t[0], t[1]))[:5]
    dep_high = sum(1 for _, v in rows if "SERIAL_DEPENDENCE_HIGH" in v["warnings"])
    boots = []
    for cd, v in rows:
        b = v.get("bootstrap")
        if b is None:
            continue
        item = {"candidate_id": cd, "targets": v["bootstrap_target_reasons"], "status": b["status"]}
        if b["status"] == "OK":
            item |= {"block_length": {k: round(x, 3) for k, x in b["block_length"].items()},
                     "sharpe": round(b["point"]["sharpe"], 6),
                     "sharpe_ci90": None if b["stationary"]["sharpe_ci"] is None else
                     [round(x, 6) for x in b["stationary"]["sharpe_ci"]],
                     "mean_amount_ci90": [round(x, 4) for x in b["stationary"]["mean_amount_ci"]],
                     "max_dd_p95": round(b["stationary"]["max_dd_distribution"]["p95"], 2),
                     "dsr_conservative": v["dsr"]["dsr_conservative"]}
        else:
            item["reason"] = b["reason"]
        boots.append(item)
    return {
        "stat_id": stat_id, "round_id": sx["round_id"], "filter_id": sx["filter_id"],
        "strategy_family": sx["strategy_family"], "component": f"{sx['component_name']} {sx['component_version']}",
        "sample_role": sx["sample_role"], "period": f"[{sx['from_date']}, {sx['to_date']})",
        "candidates": sx["n_candidates"], "n_trials": sx["n_trials"], "n_trials_round": sx["n_trials_round"],
        "var_sr": sx["var_sr"], "var_sr_count": sx["var_sr_count"], "var_sr_round": sx["var_sr_round"],
        "var_sr_round_count": sx["var_sr_round_count"], "excluded": json.loads(sx["excluded_counts_json"]),
        "sr0": sx["sr0"], "sr0_round": sx["sr0_round"], "n_eff_reference": json.loads(sx["n_eff_reference_json"]),
        "family_inputs": json.loads(sx["family_inputs_json"]), "bootstrap_reps": sx["bootstrap_reps"],
        "seed": sx["seed"], "arch_version": sx["arch_version"], "dsr_standard": q(std), "dsr_conservative": q(cons),
        "serial_dependence_high": dep_high,
        "top_by_conservative_dsr": [{"candidate_id": cd, "dsr_conservative": round(c, 6),
                                     "dsr": v["dsr"]["dsr"], "dsr_ar1": v["dsr"]["dsr_ar1"],
                                     "dsr_newey_west": v["dsr"]["dsr_newey_west"],
                                     "rho1": (v["dsr"].get("dependence") or {}).get("acf", {}).get("1"),
                                     "n_ar1": (v["dsr"].get("dependence") or {}).get("n_ar1")} for c, cd, v in top],
        "bootstrap": boots, "known_limitations": json.loads(sx["known_limitations_json"]), "note": NOTE,
    }


def run_stats(workspace: Path, round_id: str, filter_id: str, candidates: list[str] | None = None,
              reps: int = 2000, seed: int | None = None) -> StatOutcome:
    workspace = workspace.resolve()
    path = workspace / "robustlab.sqlite"
    if not path.is_file():
        return StatOutcome(EXIT_USER, "NO_WORKSPACE", f"no workspace database at {path}")
    if reps < 100:
        return StatOutcome(EXIT_USER, "INPUT_INVALID", "--reps must be at least 100")
    db = Database(path)
    try:
        store = ArtifactStore(workspace / "artifacts", db)
        fx = db.one("SELECT * FROM filter_execution WHERE filter_id = ?", (filter_id,))
        if fx is None:
            return StatOutcome(EXIT_USER, "UNKNOWN_FILTER", f"unknown filter execution {filter_id}")
        if fx["round_id"] != round_id:
            return StatOutcome(EXIT_USER, "FILTER_ROUND_MISMATCH",
                               f"filter {filter_id} belongs to round {fx['round_id']}, not {round_id}")
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (round_id,))
        job_ids = json.loads(fx["input_job_ids_json"])
        seed = default_seed(round_id, filter_id) if seed is None else int(seed)
        marks = ",".join("?" for _ in job_ids)
        rows = db.all(f"SELECT p.candidate_id, p.run_id, p.metrics_json, c.params_json, r.from_date, r.to_date, "
                      f"d.outcome FROM pass_result p JOIN candidate c USING (candidate_id) JOIN run r USING (run_id) "
                      f"JOIN decision d ON d.candidate_id = p.candidate_id AND d.filter_id = ? "
                      f"WHERE p.job_id IN ({marks}) ORDER BY p.candidate_id", (filter_id, *job_ids))
        in_round = {r["candidate_id"] for r in rows}
        unknown = sorted(set(candidates or []) - in_round)
        if unknown:
            return StatOutcome(EXIT_USER, "INPUT_INVALID", f"not a candidate of this filter execution: {unknown}")
        # P6 reads the Discovery period of this round only (P6_PLAN §5)
        bad = [r["run_id"] for r in rows if (r["from_date"], r["to_date"]) != (rnd["from_date"], rnd["to_date"])]
        if bad:
            return StatOutcome(EXIT_USER, "INPUT_INVALID", f"runs outside the round's period: {bad[:3]}")
        try:
            fam = family_scope(db, rnd, job_ids)
        except StatError as e:
            return StatOutcome(EXIT_USER, "INPUT_INVALID", str(e))
        missing = in_round - expand_round(rnd)
        if missing:
            return StatOutcome(EXIT_USER, "INPUT_INVALID",
                               f"{len(missing)} candidates are not on the round's requested grid (N would be wrong)")
        targets: dict[str, list[str]] = {}
        for r in rows:
            if r["outcome"] == "PASS":
                targets.setdefault(r["candidate_id"], []).append("P4_PASS")
        for m in db.all("SELECT DISTINCT g.medoid_candidate_id FROM parameter_region g JOIN robustness_execution x "
                        "USING (robust_id) WHERE x.filter_id = ?", (filter_id,)):
            targets.setdefault(m["medoid_candidate_id"], []).append("P5_MEDOID")
        for cd in candidates or []:
            targets.setdefault(cd, []).append("REQUESTED")
        family_hash = ids.sha256_hex(ids.canonical_json(fam["rounds"]))
        stat_id = ids.content_id("st", {"round_id": round_id, "filter_id": filter_id, "targets": sorted(targets),
                                        "reps": reps, "seed": seed, "config": CONFIG, "version": VERSION,
                                        "family_inputs": family_hash})
        if db.one("SELECT 1 FROM statistics_execution WHERE stat_id = ?", (stat_id,)):
            return StatOutcome(EXIT_OK, "ALREADY_DONE", "already evaluated with the same inputs and settings",
                               stat_id, summarize(db, stat_id))
        deposit = float(json.loads(rnd["tester_settings_json"])["deposit"])
        try:
            daily = _daily_returns(db, store, rnd, job_ids, deposit)
        except StatError as e:
            return StatOutcome(EXIT_USER, "INPUT_INVALID", str(e))
        names = [a["name"] for a in json.loads(rnd["param_space_json"])]
        s0 = dsr.sr0(fam["n_trials"], fam["var_sr"])
        s0_round = dsr.sr0(fam["n_trials_round"], fam["var_sr_round"])
        now = now_iso()
        evals = []
        ok_returns: dict[tuple[str, ...], list[np.ndarray]] = {}
        for r in rows:
            cd = r["candidate_id"]
            m = json.loads(r["metrics_json"])
            params = json.loads(r["params_json"])
            dates, ret = daily.get(tuple(float(params[n]) for n in names), ([], np.array([])))
            _, status, flags = grid_stats.score_of(m)
            sr, n = _num(m, "daily_sharpe"), _num(m, "days")
            values: dict[str, Any] = {
                "sample_role": SAMPLE_ROLE, "period": {"from": rnd["from_date"], "to": rnd["to_date"]},
                "score_status": status, "score_flags": flags, "sharpe": sr, "days": n,
                "skewness": _num(m, "daily_skewness"), "kurtosis": _num(m, "daily_kurtosis"),
                "in_var_sr": status == "OK", "counted_in_n": True, "warnings": [],
                "bootstrap_target_reasons": targets.get(cd, []), "config": CONFIG, "version": VERSION,
            }
            if status != "OK":
                reason = EXCLUSION_REASON[status]
                values["sharpe"] = None
                values["dsr"] = {k: None for k in ("dsr", "dsr_ar1", "dsr_newey_west", "dsr_bootstrap_se",
                                                   "dsr_conservative")} | {
                    "usage": dsr.USAGE, "reason": reason, "sr0": s0.value, "sr0_reason": s0.reason}
                values["dsr_round_scope"] = None
                values["bootstrap"] = ({"status": "NOT_COMPUTED", "reason": reason} if cd in targets else None)
            elif n is None or sr is None:
                return StatOutcome(EXIT_USER, "INPUT_INVALID", f"{cd} has an OK score but no day count")
            else:
                if len(ret) != int(n) or abs(float(ret.mean()) / float(ret.std(ddof=1)) - sr) > 1e-9 * max(1, abs(sr)):
                    return StatOutcome(EXIT_USER, "INPUT_INVALID",
                                       f"daily Parquet of {cd} does not reproduce its stored daily Sharpe")
                ok_returns.setdefault(tuple(dates), []).append(ret)
                boot = None
                if cd in targets:
                    boot = bootstrap.evaluate(ret, deposit, reps, candidate_seed(seed, cd))
                    values["bootstrap"] = boot
                else:
                    values["bootstrap"] = None
                se = boot["stationary"]["sharpe_se"] if boot and boot["status"] == "OK" else None
                d = dsr.deflated(sr, int(n), values["skewness"], values["kurtosis"], s0, ret, se)
                values["dsr"] = d
                rd = dsr.deflated(sr, int(n), values["skewness"], values["kurtosis"], s0_round, None)
                values["dsr_round_scope"] = {"dsr": rd["dsr"], "sr0": s0_round.value, "reason": rd["dsr_reason"]}
                if d.get("dependence") and d["dependence"]["serial_dependence_high"]:
                    values["warnings"].append("SERIAL_DEPENDENCE_HIGH")
                if d["dsr_conservative"] is not None and d["dsr_conservative"] < DSR_WARN_BELOW:
                    values["warnings"].append("LOW_DSR")
                if boot and boot["status"] == "OK" and boot["sensitivity_note"]:
                    values["warnings"].append("BOOTSTRAP_SENSITIVITY")
            evals.append({
                "evaluation_id": ids.content_id("ev", {"stat_id": stat_id, "candidate_id": cd}),
                "candidate_id": cd, "stage": STAGE, "evaluator_name": COMPONENT, "evaluator_version": VERSION,
                "config_hash": ids.sha256_hex(ids.canonical_json(CONFIG)), "input_run_ids_json": json.dumps([r["run_id"]]),
                "input_hash": ids.sha256_hex(ids.canonical_json({"filter_id": filter_id, "job_ids": job_ids,
                                                                 "family": family_hash})),
                "values_json": json.dumps(values, sort_keys=True, default=_jsonable), "filter_id": filter_id,
                "stat_id": stat_id, "created_at": now,
            })
        common = max(ok_returns.items(), key=lambda kv: len(kv[1]), default=(None, []))[1]
        n_eff = dsr.participation_ratio(np.column_stack(common)) if len(common) >= 2 else {
            "value": None, "reason": "fewer than 2 candidates with a computable Sharpe"}
        n_eff |= {"scope": "round", "usage": "reference only, not used for SR0 (ONC in P12)"}
        with db.tx():
            db.insert("statistics_execution", {
                "stat_id": stat_id, "round_id": round_id, "filter_id": filter_id,
                "strategy_family": rnd["strategy_family"], "component_name": COMPONENT, "component_version": VERSION,
                "config_json": ids.canonical_json(CONFIG), "sample_role": SAMPLE_ROLE, "from_date": rnd["from_date"],
                "to_date": rnd["to_date"], "n_trials": fam["n_trials"], "n_trials_round": fam["n_trials_round"],
                "var_sr": fam["var_sr"], "var_sr_count": fam["var_sr_count"], "var_sr_round": fam["var_sr_round"],
                "var_sr_round_count": fam["var_sr_round_count"],
                "excluded_counts_json": json.dumps({"family": fam["excluded"], "round": fam["excluded_round"]}),
                "sr0": s0.value, "sr0_round": s0_round.value, "n_eff_reference_json": json.dumps(n_eff),
                "family_inputs_json": json.dumps({"rounds": fam["rounds"], "trial_ledger_sum": fam["trial_ledger_sum"],
                                                  "distinct_config_periods": fam["distinct_config_periods"],
                                                  "periods": fam["periods"], "sr0_reason": s0.reason,
                                                  "sr0_round_reason": s0_round.reason,
                                                  "mixed_period_lengths": len(fam["periods"]) > 1}),
                "bootstrap_reps": reps, "seed": seed, "bootstrap_targets_json": json.dumps(targets, sort_keys=True),
                "arch_version": bootstrap.ARCH_VERSION, "known_limitations_json": json.dumps(list(dsr.KNOWN_LIMITATIONS)),
                "n_candidates": len(evals), "created_at": now,
            })
            for e in evals:
                db.insert("evaluation", e)
        return StatOutcome(EXIT_OK, "DONE", "", stat_id, summarize(db, stat_id))
    finally:
        db.close()


def _jsonable(o: Any) -> Any:
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, tuple):
        return list(o)
    return str(o)
