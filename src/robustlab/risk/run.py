"""P9 risk evaluation and allocation run (P9_PLAN §1-§5, C77-C79).

Input: a P8a validation plan (its Discovery / V0 / V1 single tests of each candidate, fixed lot) and a broker
snapshot. The holdout is never read: only the plan's validation runs are used (Q4).

Eligibility (principle 3 of 2026-10-11): a lot is computed only for a candidate with a P8a PASS decision on a
non-diagnostic plan. P8b: a production OOS FAIL -> NOT_ELIGIBLE_OOS_FAILED; a production OOS PASS -> confidence 1.0;
no OOS -> 0.5. Rehearsal OOS results never raise the confidence (a rehearsal FAIL still blocks; a rehearsal PASS
counts as "not run"). Every other candidate is DIAGNOSTIC: per-lot risk figures only, no lot, no decision.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from robustlab.config.loader import ConfigError, load_risk_profile
from robustlab.core import ids
from robustlab.metrics.engine import CLOSING_ENTRIES, DEAL_BUY, DEAL_SELL, ENTRY_IN
from robustlab.mt5 import telemetry_reader as tr
from robustlab.risk import engine as eng
from robustlab.risk import sizing as sz
from robustlab.risk import snapshot as snp
from robustlab.storage.artifacts import ArtifactStore
from robustlab.storage.db import Database, now_iso
from robustlab.validation import run as vrun

COMPONENT = "risk"
VERSION = f"p9-{eng.ENGINE_VERSION}+{sz.SIZING_VERSION}"
STAGE = "ALLOCATION"
EXIT_OK, EXIT_INPUT, EXIT_RUNTIME = 0, 1, 2

ELIGIBLE, DIAGNOSTIC = "ELIGIBLE", "DIAGNOSTIC"
NOT_ELIGIBLE_OOS_FAILED = "NOT_ELIGIBLE_OOS_FAILED"
RISK_UNIT_UNDEFINED = "RISK_UNIT_UNDEFINED"

REASON_CODES: dict[str, tuple[str, str, str]] = {
    sz.ALLOCATED: ("allocation", "lot computed within every limit (rounded down)", "INFO"),
    sz.INFEASIBLE_MIN_LOT: ("allocation", "the safe target lot is below the broker minimum lot; not rounded up", "FAIL"),
    sz.MARGIN_BUDGET_EXCEEDED: ("allocation", "even the minimum lot exceeds the margin budget", "FAIL"),
    sz.NO_ALLOCATION_MU_LCB: ("allocation", "lower bound of the mean P&L <= 0: no allocation", "FAIL"),
    NOT_ELIGIBLE_OOS_FAILED: ("allocation", "the candidate failed the OOS check (P8b): no allocation", "FAIL"),
    RISK_UNIT_UNDEFINED: ("data", "not a fixed-lot backtest (or no trades): risk per lot is undefined", "FAIL"),
    "MARGIN_PRICE_SNAPSHOT": ("allocation", "margin per lot at the snapshot price (warning)", "WARN"),
    "EQUITY_OVERRIDDEN": ("allocation", "equity given on the command line, not from the snapshot (warning)", "WARN"),
}

NOT_EVIDENCE_TEXT = ("Looking safe in this P9 simulation is NOT evidence of an edge. P9 only bounds the damage if the "
                     "candidate is traded; whether it has an edge is decided by P8a / P8b (and P10).")
ASSUMPTION_TEXT = ("The stress sizes (losing streak x1.5, AR(1) 0.3, 63 days x2 volatility, spread x2, no edge and "
                   "their combinations) are assumptions fixed in advance, not measurements of the market. The "
                   "probabilities are estimates under these assumptions, not the future probabilities of this EA.")
D5_RESULT_TEXT = ("Simulation result: with future volatility 1.5 times higher for a whole year, the pre-registered DD "
                  "criterion was met in the pre-implementation simulation (P9_PLAN §6.6, after S63 was added). This "
                  "is a result under those simulated conditions only.")
D5_LIMIT_TEXT = ("Known limitation (not verified): tolerance to a PERMANENT change of the market regime (volatility "
                 "rising more than 1.5 times, or other lasting changes) has NOT been sufficiently verified; no scenario "
                 "models a regime change. Monitoring after going live detects a change only after it has happened and "
                 "does not prevent the losses before it is detected.")
S1A_TEXT = ("S1a (classic trade shuffle, closed P&L only, no floating loss) is shown for reference only and limits "
            "nothing: on closed equity the same DD threshold measures a smaller quantity (P9_PLAN §6.4).")
DIAGNOSTIC_TEXT = ("DIAGNOSTIC: this candidate has not passed P8a (or the plan is diagnostic). It is not a candidate "
                   "for allocation; only the risk per lot is shown, and no lot is computed.")


class RiskError(RuntimeError):
    pass


@dataclass
class RiskOutcome:
    exit_code: int
    status: str
    message: str = ""
    risk_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


# ---------- history ----------

def _artifact(db: Database, store: ArtifactStore, job_id: str, role: str) -> str | None:
    row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = ?", (job_id, role))
    return store.read(row["sha256"]).decode("utf-8-sig") if row else None


def period_series(db: Database, store: ArtifactStore, job_id: str) -> dict[str, Any]:
    """Per-lot daily series of one single test. Raises RiskError(RISK_UNIT_UNDEFINED) if the lot is not fixed."""
    env = json.loads(_artifact(db, store, job_id, "telemetry_env"))
    deals = tr.parse_csv_text(_artifact(db, store, job_id, "telemetry_deals"), tr.DEALS_COLUMNS, "deals")
    daily_text = _artifact(db, store, job_id, "telemetry_daily")
    daily = tr.parse_csv_text(daily_text, tr.DAILY_COLUMNS, "daily") if daily_text else []
    trade = [d for d in deals if int(d["type"]) in (DEAL_BUY, DEAL_SELL)]
    vols = {float(d["volume"]) for d in trade if int(d["entry"]) == ENTRY_IN}
    if len(vols) != 1 or not daily:
        raise RiskError(f"{RISK_UNIT_UNDEFINED}: job {job_id} opens with volumes {sorted(vols)} "
                        f"({len(daily)} daily rows); a fixed-lot backtest is required")
    lot = vols.pop()
    dep = float(env["initial_balance"])
    eq = np.array([float(r["equity_close"]) for r in daily])
    eq_min = np.array([float(r["equity_min"]) for r in daily])
    prev = np.concatenate([[dep], eq[:-1]])
    x = (eq - prev) / lot
    d = np.minimum(0.0, eq_min - np.minimum(prev, eq)) / lot
    pos = np.array([float(r["positions_max"]) for r in daily])
    closes = [t for t in trade if int(t["entry"]) in CLOSING_ENTRIES]
    by_day: dict[str, int] = {}
    for t in closes:
        day = t["time"][:10]
        by_day[day] = by_day.get(day, 0) + 1
    c = np.array([float(by_day.get(r["date"], 0)) for r in daily])
    pnl = np.array([(float(t["profit"]) + float(t["commission"]) + float(t["swap"]) + float(t["fee"]))
                    / float(t["volume"]) for t in closes])
    tracking = env.get("tracking") or {}
    return {"x": x, "d": d, "c": c, "pos": pos, "trades": pnl, "lot": lot, "deposit": dep, "days": len(x),
            "max_floating_loss_per_lot": (abs(float(tracking["max_floating_loss"])) / lot
                                          if tracking.get("max_floating_loss") is not None else None),
            "min_margin_level": tracking.get("min_margin_level")}


def history_of(db: Database, store: ArtifactStore, plan: dict[str, Any], jobs: dict, cd: str) -> tuple[eng.History,
                                                                                                         dict]:
    names = [vrun.DISCOVERY] + [p["name"] for p in plan["periods"]]
    parts = []
    for n in names:
        if (cd, n) not in jobs:
            raise RiskError(f"no successful single test for {cd} in {n}")
        parts.append(period_series(db, store, jobs[(cd, n)]["job_id"]))
    h = eng.History(x=np.concatenate([p["x"] for p in parts]), d=np.concatenate([p["d"] for p in parts]),
                    c=np.concatenate([p["c"] for p in parts]), pos=np.concatenate([p["pos"] for p in parts]),
                    seg_lens=tuple(p["days"] for p in parts), trades=np.concatenate([p["trades"] for p in parts]))
    obs = {"periods": {n: {"job_id": jobs[(cd, n)]["job_id"], "days": p["days"], "lot": p["lot"],
                           "trades": len(p["trades"])} for n, p in zip(names, parts, strict=True)},
           "max_floating_loss_per_lot": max((p["max_floating_loss_per_lot"] or 0.0) for p in parts),
           "min_margin_level": min((p["min_margin_level"] for p in parts if p["min_margin_level"] is not None),
                                   default=None),
           "longest_losing_days": eng.longest_losing_run(h.x, h.seg_lens),
           "longest_losing_trades": _longest_run(h.trades < 0)}
    return h, obs


def _longest_run(a: np.ndarray) -> int:
    best = run = 0
    for v in a:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


# ---------- eligibility ----------

def eligibility(db: Database, vp: Any, cd: str, prof: Any) -> tuple[str, float | None, dict[str, Any]]:
    if vp["diagnostic"]:
        return DIAGNOSTIC, None, {"why": "diagnostic validation plan"}
    dec = db.one("SELECT outcome FROM decision WHERE plan_id = ? AND stage = 'VALIDATION' AND candidate_id = ?",
                 (vp["plan_id"], cd))
    if dec is None or dec["outcome"] != "PASS":
        return DIAGNOSTIC, None, {"why": f"P8a outcome {dec['outcome'] if dec else 'none'}"}
    oos = db.all("SELECT d.outcome, d.manifest_id FROM decision d WHERE d.stage = 'OOS' AND d.candidate_id = ?", (cd,))
    if any(r["outcome"] != "PASS" for r in oos):
        return NOT_ELIGIBLE_OOS_FAILED, None, {"oos": [dict(r) for r in oos]}
    reh = []
    for r in db.all("SELECT e.values_json, e.manifest_id FROM evaluation e JOIN oos_manifest m USING (manifest_id) "
                    "WHERE e.stage = 'OOS' AND e.candidate_id = ? AND m.rehearsal = 1", (cd,)):
        failed = any(g["status"] == "FAIL" for g in json.loads(r["values_json"])["gates"])
        reh.append({"manifest_id": r["manifest_id"], "rehearsal_outcome": "FAIL" if failed else "PASS"})
        if failed:
            return NOT_ELIGIBLE_OOS_FAILED, None, {"rehearsal_oos": reh}
    if oos:
        return ELIGIBLE, prof.confidence_oos_pass, {"oos": [dict(r) for r in oos]}
    return ELIGIBLE, prof.confidence_no_oos, {"oos": "not run", "rehearsal_oos": reh}


# ---------- evaluation ----------

def evaluate(h: eng.History, snap: dict[str, Any], equity: float, prof: Any, seed: int,
             compute_lots: bool) -> dict[str, Any]:
    """Scenario figures; with compute_lots also the safe lot and the critical lots needed to re-check it."""
    rng = np.random.Generator(np.random.PCG64(seed))
    stress = eng.Stress(prof.streak_factor, prof.ar_rho, prof.shock_days, prof.shock_factor, prof.spread_factor)
    acc = snap["account"]
    so_mode = acc["margin_so_mode"]
    per_scen: dict[str, Any] = {}
    crits: dict[str, dict[str, np.ndarray]] = {}
    for name, X, D, P in eng.scenarios(h, rng, prof.n_paths, prof.horizon_days, snap["stress_cost_per_lot"], stress):
        item: dict[str, Any] = {"role": "reference" if name in eng.REFERENCE else
                                ("survival limits only" if name == "SC" else "DD and survival limits"),
                                "per_lot": eng.path_profile(X, D)}
        if compute_lots and name not in eng.REFERENCE:
            Xe, De = X / equity, D / equity
            cr = {"ruin": eng.crit_ruin(Xe, De, prof.ruin_loss)}
            if so_mode == snp.STOPOUT_MODE_PERCENT:
                cr["stop_out"] = eng.crit_stop_out(Xe, De, P, so_percent=float(acc["margin_so_so"]),
                                                   margin=snap["margin_per_lot"] / equity)
            else:
                cr["stop_out"] = eng.crit_stop_out(Xe, De, P, so_money_frac=float(acc["margin_so_so"]) / equity)
            if name in eng.LIMITED_DD:
                cr["dd"] = eng.crit_dd(Xe, De, prof.max_dd)
            p_of = {"dd": prof.dd_alpha, "ruin": prof.ruin_p, "stop_out": prof.stop_out_p}
            item["allowed_lot"] = {k: eng.allowed(v, p_of[k]) for k, v in cr.items()}
            crits[name] = cr
        per_scen[name] = item
    out: dict[str, Any] = {"scenarios": per_scen, "block_length": eng.mean_block_length(h)}
    if compute_lots:
        lims = {f"{n}:{k}": v for n, it in per_scen.items() for k, v in it.get("allowed_lot", {}).items()}
        key = min(lims, key=lims.get)
        out |= {"lot_safe": lims[key], "safe_binding": key, "_crits": crits}
    return out


def recheck(crits: dict[str, dict[str, np.ndarray]], lot: float, prof: Any) -> dict[str, Any]:
    """Probabilities at the final lot (upper bounds); any bound above its limit is a bug -> refuse."""
    p_of = {"dd": prof.dd_alpha, "ruin": prof.ruin_p, "stop_out": prof.stop_out_p}
    out: dict[str, Any] = {}
    for name, cr in crits.items():
        out[name] = {}
        for k, v in cr.items():
            cnt = int((v <= lot).sum())
            ub = eng.upper_bound(cnt, len(v))
            if ub > p_of[k] + 1e-12:
                raise AssertionError(f"final lot {lot} breaks {name}:{k} (upper bound {ub} > {p_of[k]})")
            out[name][k] = {"share": cnt / len(v), "upper_bound_95": ub, "limit": p_of[k]}
    return out


def _seed(risk_id: str, cd: str) -> int:
    return int(hashlib.sha256(f"{risk_id}|{cd}".encode()).hexdigest()[:8], 16)


def run(workspace: Path, plan_id: str, snapshot_path: Path, profile_path: Path, profile_name: str = "default",
        equity: float | None = None, candidate: str | None = None) -> RiskOutcome:
    workspace = workspace.resolve()
    try:
        prof, prof_hash = load_risk_profile(profile_path, profile_name)
        snap, snap_hash = snp.load(snapshot_path)
    except (ConfigError, snp.SnapshotError, OSError, ValueError, KeyError) as e:
        return RiskOutcome(EXIT_INPUT, "INPUT_INVALID", str(e))
    if equity is not None and not equity > 0:
        return RiskOutcome(EXIT_INPUT, "INPUT_INVALID", "--equity must be > 0")
    db = Database(workspace / "robustlab.sqlite")
    try:
        vp = db.one("SELECT * FROM validation_plan WHERE plan_id = ?", (plan_id,))
        if vp is None:
            return RiskOutcome(EXIT_INPUT, "INPUT_INVALID", f"unknown validation plan {plan_id}")
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (vp["round_id"],))
        if rnd["symbol"] != snap["symbol"]:
            return RiskOutcome(EXIT_INPUT, "INPUT_INVALID",
                               f"snapshot symbol {snap['symbol']} differs from the round symbol {rnd['symbol']}")
        if not db.one("SELECT 1 FROM evaluation WHERE plan_id = ? AND stage = 'VALIDATION'", (plan_id,)):
            return RiskOutcome(EXIT_INPUT, "NOT_EVALUATED", f"run `rlab validate run {plan_id}` first")
        cands = json.loads(vp["candidates_json"])
        if candidate:
            if candidate not in cands:
                return RiskOutcome(EXIT_INPUT, "INPUT_INVALID", f"{candidate} is not in plan {plan_id}")
            cands = [candidate]
        eq = float(equity) if equity is not None else float(snap["account"]["equity"])
        risk_id = ids.content_id("rk", {"plan_id": plan_id, "snapshot": snap_hash, "profile": prof_hash,
                                        "equity": eq, "candidates": cands, "version": VERSION})
        if db.one("SELECT 1 FROM risk_execution WHERE risk_id = ?", (risk_id,)):
            return RiskOutcome(EXIT_OK, "ALREADY_DONE", "", risk_id, summarize(db, risk_id))
        store = ArtifactStore(workspace / "artifacts", db)
        plan = json.loads(vp["plan_json"])
        jobs = vrun._jobs(db, plan_id)
        results: dict[str, dict[str, Any]] = {}
        for cd in cands:
            status, weight, elig = eligibility(db, vp, cd, prof)
            seed = _seed(risk_id, cd)
            res: dict[str, Any] = {"eligibility": status, "eligibility_detail": elig, "seed": seed}
            try:
                h, obs = history_of(db, store, plan, jobs, cd)
            except RiskError as e:
                res |= {"outcome": RISK_UNIT_UNDEFINED if status == ELIGIBLE else status, "error": str(e)}
                results[cd] = res
                continue
            ev_ = evaluate(h, snap, eq, prof, seed, compute_lots=(status == ELIGIBLE))
            crits = ev_.pop("_crits", None)
            res |= {"observed": obs, **ev_}
            if status == ELIGIBLE:
                rng = np.random.Generator(np.random.PCG64(seed ^ 0x5EED))
                mu = sz.mu_lcb(h.x, h.seg_lens, ev_["block_length"], prof.mu_lcb_reps, prof.mu_lcb_quantile, rng)
                loss = -h.trades
                si = snap["symbol_info"]
                s = sz.size(equity=eq, lot_safe=ev_["lot_safe"], safe_binding=ev_["safe_binding"], mu=mu,
                            var=float(h.x.var(ddof=1)), trade_loss_q99=float(np.quantile(loss, 0.99)) if len(loss) else 0.0,
                            margin_per_lot=snap["margin_per_lot"], max_positions=float(h.pos.max()),
                            volume_min=si["volume_min"], volume_step=si["volume_step"], volume_max=si["volume_max"],
                            kelly_fraction=prof.kelly_fraction, cap_per_trade=prof.cap_per_trade,
                            margin_budget=prof.margin_budget, confidence_weight=weight, ramp_up=prof.ramp_up)
                res |= {"outcome": s.outcome, "lot": s.lot, "lot_target": s.lot_target, "lot_raw": s.lot_raw,
                        "binding_constraint": s.binding, "limits": s.limits, "sizing_notes": s.notes,
                        "mu_lcb_per_lot": mu, "confidence_weight": weight, "ramp_up": prof.ramp_up}
                if s.outcome == sz.ALLOCATED:
                    res["at_final_lot"] = recheck(crits, s.lot, prof)
                    res["dd_vs_equity_at_final_lot"] = {
                        n: {q: it["per_lot"][f"max_dd_per_lot_{q}"] * s.lot / eq for q in ("q95", "q99")}
                        for n, it in ev_["scenarios"].items()}
            else:
                res["outcome"] = status
            results[cd] = res
        _persist(db, risk_id, vp, rnd, cands, profile_name, prof, prof_hash, snap, snap_hash, eq, equity is not None,
                 results)
        return RiskOutcome(EXIT_OK, "DONE", "", risk_id, summarize(db, risk_id))
    except RiskError as e:
        return RiskOutcome(EXIT_RUNTIME, "FAILED", str(e))
    finally:
        db.close()


def _persist(db: Database, risk_id: str, vp: Any, rnd: Any, cands: list[str], profile_name: str, prof: Any,
             prof_hash: str, snap: dict, snap_hash: str, eq: float, overridden: bool,
             results: dict[str, dict[str, Any]]) -> None:
    now = now_iso()
    with db.tx():
        for code, (cat, desc, sev) in REASON_CODES.items():
            db.insert("reason_code", {"code": code, "category": cat, "description": desc, "severity": sev},
                      if_absent=True)
        db.insert("risk_execution", {
            "risk_id": risk_id, "plan_id": vp["plan_id"], "round_id": vp["round_id"], "symbol": snap["symbol"],
            "candidates_json": json.dumps(cands), "profile_name": profile_name,
            "profile_json": json.dumps(prof.model_dump(mode="json")), "profile_hash": prof_hash,
            "snapshot_json": json.dumps(snap, sort_keys=True), "snapshot_hash": snap_hash, "equity": eq,
            "equity_overridden": int(overridden), "component_version": VERSION, "created_at": now})
        for cd, res in results.items():
            db.insert("risk_result", {"risk_id": risk_id, "candidate_id": cd, "status": res["outcome"],
                                      "seed": res["seed"], "values_json": json.dumps(res, default=vrun._jsonable),
                                      "created_at": now})
            if res["eligibility"] == DIAGNOSTIC:
                continue  # principle 3: no decision for a candidate that has not passed P8a
            warnings = ["MARGIN_PRICE_SNAPSHOT"] + (["EQUITY_OVERRIDDEN"] if overridden else [])
            db.insert("decision", {
                "decision_id": ids.content_id("dc", {"risk_id": risk_id, "candidate_id": cd}),
                "candidate_id": cd, "stage": STAGE,
                "outcome": "ALLOCATED" if res["outcome"] == sz.ALLOCATED else "NOT_ALLOCATED",
                "component_name": COMPONENT, "component_version": VERSION,
                "reason_codes_json": json.dumps([res["outcome"]]), "warning_codes_json": json.dumps(warnings),
                "details_json": json.dumps({k: res.get(k) for k in ("lot", "lot_target", "lot_raw",
                                                                      "binding_constraint", "limits",
                                                                      "safe_binding", "confidence_weight",
                                                                      "ramp_up", "mu_lcb_per_lot", "sizing_notes")}
                                           | {"risk_id": risk_id, "equity": eq, "snapshot_hash": snap_hash,
                                              "profile_hash": prof_hash}, default=vrun._jsonable),
                "filter_id": None, "plan_id": vp["plan_id"], "risk_id": risk_id, "created_at": now})


def summarize(db: Database, risk_id: str) -> dict[str, Any]:
    ex = db.one("SELECT * FROM risk_execution WHERE risk_id = ?", (risk_id,))
    if ex is None:
        raise RiskError(f"unknown risk execution {risk_id}")
    snap = json.loads(ex["snapshot_json"])
    results = []
    for r in db.all("SELECT * FROM risk_result WHERE risk_id = ? ORDER BY candidate_id", (risk_id,)):
        v = json.loads(r["values_json"])
        results.append({"candidate_id": r["candidate_id"], **v})
    return {"risk_id": risk_id, "plan_id": ex["plan_id"], "symbol": ex["symbol"], "equity": ex["equity"],
            "equity_overridden": bool(ex["equity_overridden"]), "account_currency": snap["account"]["currency"],
            "snapshot_taken_at": snap.get("taken_at"), "volume_min": snap["symbol_info"]["volume_min"],
            "volume_step": snap["symbol_info"]["volume_step"], "margin_per_lot": snap["margin_per_lot"],
            "stop_out": {"mode": snap["account"]["margin_so_mode"], "level": snap["account"]["margin_so_so"]},
            "profile": json.loads(ex["profile_json"]), "version": ex["component_version"],
            "created_at": ex["created_at"], "results": results,
            "notes": [NOT_EVIDENCE_TEXT, ASSUMPTION_TEXT, D5_RESULT_TEXT, D5_LIMIT_TEXT, S1A_TEXT]}
