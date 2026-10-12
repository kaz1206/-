"""P10 executions: run, persist (append-only), summarize (P10_PLAN §1.3, §3).

Modes are fixed in configs/null_archetypes.yaml and cannot be changed from the command line:
  judgement    1,000 rounds, seed 20261013, ONCE per archetype and configuration (a second run is refused)
  reproduction 400 rounds, seed 20261012, prototype bootstrap seeding (checks the code against the pre-simulation)
  power        200 rounds with the archetype's edge (recorded only)
An execution whose S1 check fails (|mean gross P&L| > 3 SE under H0) is INVALID and gives no judgement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from robustlab.core import ids
from robustlab.nulltest import pipeline as pl
from robustlab.nulltest import sim
from robustlab.storage.db import Database, now_iso

COMPONENT = "nulltest"
VERSION = f"p10-{sim.SIM_VERSION}+{pl.PIPELINE_VERSION}"
MODES = ("judgement", "reproduction", "power")
EXIT_OK, EXIT_INPUT = 0, 1

NOT_ERROR_RATE_TEXT = ("The chance pass rate is an estimate under THIS null model and THIS archetype; it is not the "
                       "error rate of a real EA.")
NOT_MODELLED_TEXT = ("Not modelled: structures absent from the price model (lasting regime changes, gaps, slippage, "
                     "the broker's stop-out processing, tick granularity finer than 8 steps per H4 bar).")
S1_TEXT = ("S1 (mean gross P&L within 3 SE of 0 under H0) is a necessary diagnostic, not a proof that the null model "
           "is right (P10_PLAN §1.4).")
MARTINGALE_TEXT = ("Martingale / grid passers that are ruined in the next year are a WARNING; with few passers the "
                   "share is not a ruin probability. P9 refuses variable-lot EAs: an allocation safeguard, not a "
                   "guarantee that the EA is safe.")


class NullError(RuntimeError):
    pass


@dataclass
class NullOutcome:
    exit_code: int
    status: str
    message: str = ""
    null_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)


def load_config(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    cfg = yaml.safe_load(raw)
    for k in ("account", "price", "days", "chunk", "criterion", "modes", "archetypes"):
        if k not in cfg:
            raise NullError(f"{path}: missing {k}")
    if cfg["modes"]["judgement"]["rounds"] < 400:
        raise NullError("the judgement run needs at least 400 rounds")
    return cfg, ids.sha256_hex(ids.canonical_json(cfg))


def verdict(res: dict[str, Any], crit: dict[str, Any], mode: str) -> str:
    z = res["s1"]["z"]
    if z is None or abs(z) > crit["s1_max_se"]:
        return "INVALID_S1"
    if mode != "judgement":
        return "RECORDED"
    return "MET" if res["ci"]["upper_one_sided"] <= crit["max_upper_bound"] else "NOT_MET"


def run(workspace: Path, archetype: str, mode: str, config_path: Path, profiles_path: Path,
        progress=None) -> NullOutcome:
    if mode not in MODES:
        return NullOutcome(EXIT_INPUT, "INPUT_INVALID", f"mode must be one of {MODES}")
    try:
        cfg, cfg_hash = load_config(config_path)
        prof_hash = ids.sha256_hex(profiles_path.read_bytes())
    except (OSError, NullError, yaml.YAMLError) as e:
        return NullOutcome(EXIT_INPUT, "INPUT_INVALID", str(e))
    if archetype not in cfg["archetypes"]:
        return NullOutcome(EXIT_INPUT, "INPUT_INVALID", f"unknown archetype {archetype} (have {sorted(cfg['archetypes'])})")
    md = cfg["modes"][mode]
    null_id = ids.content_id("nl", {"archetype": archetype, "mode": mode, "config": cfg_hash, "profiles": prof_hash,
                                    "version": VERSION})
    db = Database(workspace.resolve() / "robustlab.sqlite")
    try:
        if db.one("SELECT 1 FROM null_execution WHERE null_id = ?", (null_id,)):
            return NullOutcome(EXIT_OK, "ALREADY_DONE", "", null_id, summarize(db, null_id))
        if mode == "judgement":
            prior = db.one("SELECT null_id FROM null_execution WHERE archetype = ? AND mode = 'judgement' AND "
                           "config_hash = ?", (archetype, cfg_hash))
            if prior:
                return NullOutcome(EXIT_INPUT, "ALREADY_JUDGED", f"judgement for {archetype} exists: {prior['null_id']}",
                                   prior["null_id"], summarize(db, prior["null_id"]))
    finally:
        db.close()
    st = pl.setup_of(cfg, profiles_path)
    arch_index = sorted(cfg["archetypes"]).index(archetype) + 1
    seeding = "prototype" if mode == "reproduction" else "global"
    res = pl.execute(archetype, cfg["archetypes"][archetype], st, md["kind"], int(md["rounds"]), int(md["seed"]),
                     arch_index, seeding, progress)
    v = verdict(res, cfg["criterion"], mode)
    db = Database(workspace.resolve() / "robustlab.sqlite")
    try:
        db.insert("null_execution", {
            "null_id": null_id, "archetype": archetype, "mode": mode, "kind": md["kind"], "rounds": int(md["rounds"]),
            "seed": int(md["seed"]), "evidence_seeding": seeding, "config_json": json.dumps(cfg),
            "config_hash": cfg_hash, "profiles_hash": prof_hash, "component_name": COMPONENT,
            "component_version": VERSION, "verdict": v, "criterion_json": json.dumps(cfg["criterion"]),
            "result_json": json.dumps(res), "created_at": now_iso()})
        return NullOutcome(EXIT_OK, "DONE", "", null_id, summarize(db, null_id))
    finally:
        db.close()


def summarize(db: Database, null_id: str) -> dict[str, Any]:
    r = db.one("SELECT * FROM null_execution WHERE null_id = ?", (null_id,))
    if r is None:
        raise NullError(f"unknown null execution {null_id}")
    res = json.loads(r["result_json"])
    notes = [NOT_ERROR_RATE_TEXT, NOT_MODELLED_TEXT, S1_TEXT]
    if json.loads(r["config_json"])["archetypes"][r["archetype"]]["type"] in ("grid", "martingale"):
        notes.append(MARTINGALE_TEXT)
    return {"null_id": null_id, "archetype": r["archetype"], "mode": r["mode"], "kind": r["kind"],
            "rounds": r["rounds"], "seed": r["seed"], "evidence_seeding": r["evidence_seeding"],
            "verdict": r["verdict"], "criterion": json.loads(r["criterion_json"]), "version": r["component_version"],
            "created_at": r["created_at"], "result": res, "notes": notes}
