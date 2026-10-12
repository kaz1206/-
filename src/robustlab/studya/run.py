"""Study A measurements (SPEC §6): S (H0, 1,000 rounds) and P (6 edge settings x 400 rounds), each ONCE, plus the pilot.

Results go to docs/research/study_a/results/ (append-only files, SPEC §10 A8): <name>.json (summary with the config
and calibration hashes, versions, seeds, timing, Clopper-Pearson intervals, S1, funnel) and <name>_trace.jsonl.gz
(one line per round with every candidate's stage outcomes). A second run of a main measurement is refused.
Rounds are independent (per-round seeds), so they run in parallel without changing the result (SPEC §10 A9).
"""

from __future__ import annotations

import gzip
import json
import math
import multiprocessing as mp
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import beta

from robustlab.core import ids
from robustlab.studya import calibrate as C
from robustlab.studya import evaluate as E
from robustlab.studya import prices, t1

RESULTS = E.REPO / "docs" / "research" / "study_a" / "results"
VERSION = f"studyA-{prices.PRICES_VERSION}+{t1.T1_VERSION}+{E.EVALUATE_VERSION}"

_ST: E.Setup | None = None


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> dict[str, float]:
    a = (1 - conf) / 2
    return {"lower": float(beta.ppf(a, k, n - k + 1)) if k else 0.0,
            "upper": float(beta.ppf(1 - a, k + 1, n - k)) if k < n else 1.0,
            "upper_one_sided": float(beta.ppf(conf, k + 1, n - k)) if k < n else 1.0}


def _init(cfg_path: str) -> None:
    global _ST
    _ST = E.setup(Path(cfg_path))


def _one(args: tuple[str, dict[str, Any], float, int]) -> dict[str, Any]:
    name, m, scale, i = args
    assert _ST is not None
    t0 = time.time()
    p = E.round_path(_ST, int(m["base_seed"]), i, m.get("edge"), scale, m.get("edge_seed"))
    out = E.evaluate_round(_ST, p, E.synthetic_ids(_ST, name, i))
    out["round"] = i
    out["seconds"] = round(time.time() - t0, 3)
    return out


def scale_of(st: E.Setup, m: dict[str, Any], calib: dict[str, Any] | None) -> float:
    if m.get("edge") is None:
        return 0.0
    if calib is None:
        raise RuntimeError("an edge measurement needs the calibration file (rlab studya calibrate)")
    return float(calib["edges"][m["edge"]][str(float(m["strength"]))]["scale"])


def execute(cfg_path: Path, name: str, m: dict[str, Any], *, workers: int = 4, rounds: int | None = None,
            calib_path: Path | None = None, progress=None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    st = E.setup(cfg_path)
    calib, calib_hash = None, None
    if m.get("edge") is not None or calib_path is not None:     # S records the calibration it ran under too
        cp = calib_path or RESULTS / "calibration.json"
        text = cp.read_text(encoding="utf-8")
        calib, calib_hash = json.loads(text), ids.sha256_hex(text)
        if calib["config_hash"] != st.cfg_hash:
            raise RuntimeError("the calibration was made with another study_a.yaml")
    scale = scale_of(st, m, calib)
    n = int(rounds if rounds is not None else m["rounds"])
    t0 = time.time()
    jobs = [(name, m, scale, i) for i in range(n)]
    out: list[dict[str, Any]] = []
    if workers <= 1:
        _init(str(cfg_path))
        for k, j in enumerate(jobs):
            out.append(_one(j))
            if progress and (k + 1) % 10 == 0:
                progress(k + 1, n)
    else:
        with mp.get_context("spawn").Pool(workers, initializer=_init, initargs=(str(cfg_path),)) as pool:
            for k, r in enumerate(pool.imap(_one, jobs, chunksize=1)):
                out.append(r)
                if progress and (k + 1) % 10 == 0:
                    progress(k + 1, n)
    wall = time.time() - t0
    return summarize(st, name, m, scale, calib_hash, out, wall, workers), out


def summarize(st: E.Setup, name: str, m: dict[str, Any], scale: float, calib_hash: str | None,
              rounds: list[dict[str, Any]], wall: float, workers: int) -> dict[str, Any]:
    n = len(rounds)
    k = sum(1 for r in rounds if r["detected"])
    crit = st.cfg["criterion"]
    ci = clopper_pearson(k, n, float(crit["confidence"]))
    g = np.array([r["gross_mean"] for r in rounds])
    se = float(g.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    reasons: dict[str, Counter] = {"p4": Counter(), "p7": Counter(), "p8a": Counter()}
    for r in rounds:
        for row in r["rows"]:
            reasons["p4"].update(row["p4"]["reasons"])
            if "p7" in row:
                reasons["p7"].update(row["p7"]["reasons"] or ["SELECTED"])
            if "p8a" in row:
                reasons["p8a"].update(row["p8a"]["reasons"] or ["PASS"])
    s = {"name": name, "version": VERSION, "config_hash": st.cfg_hash, "calibration_hash": calib_hash,
         "measurement": m, "edge_scale": scale, "rounds": n, "detected_rounds": k, "rate": k / n if n else None,
         "ci": ci, "criterion": crit,
         "funnel_mean_per_round": {x: float(np.mean([r[x] for r in rounds])) for x in
                                   ("p4_pass", "gate_pass", "selected", "p8a_pass")},
         "rounds_with": {"p4_pass": sum(1 for r in rounds if r["p4_pass"]),
                         "selected": sum(1 for r in rounds if r["selected"]),
                         "p8a_pass": k},
         "reason_counts": {s_: dict(c.most_common()) for s_, c in reasons.items()},
         "s1": {"gross_mean": float(g.mean()), "se": se, "z": float(g.mean() / se) if se and se > 0 else None},
         "timing": {"wall_seconds": round(wall, 1), "workers": workers,
                    "round_seconds_mean": float(np.mean([r["seconds"] for r in rounds])),
                    "round_seconds_max": float(np.max([r["seconds"] for r in rounds]))},
         "n_trials": st.cfg["dsr_n_trials"],
         "notes": ["Rates are estimates under THIS synthetic model and THIS archetype, not the error or detection "
                   "rate of a real EA on a real market (SPEC §1).",
                   "Not replicable on synthetic data: the P8a reproducibility re-run (always PASS) and the exposure "
                   "ledger. Approximation: V[SR] of DSR from the round's 60 results (SPEC §5.1).",
                   "No swap in the model: study B (real EURUSD) pays swaps; this detection rate can be optimistic "
                   "(SPEC §7.3)."]}
    if m.get("edge") is None:
        s["verdict"] = ("INVALID_S1" if s["s1"]["z"] is None or abs(s["s1"]["z"]) > float(crit["s1_max_se"]) else
                        ("MET" if ci["upper_one_sided"] <= float(crit["max_upper_bound"]) else "NOT_MET"))
    return s


def save(summary: dict[str, Any], rounds: list[dict[str, Any]], out_dir: Path, stem: str,
         refuse_existing: bool = True) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{stem}.json"
    if refuse_existing and js.exists():
        raise FileExistsError(f"{js} exists: each measurement runs once (SPEC §6)")
    with gzip.open(out_dir / f"{stem}_trace.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in rounds:
            f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    js.write_text(json.dumps(summary, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return js


def already(name: str) -> bool:
    return (RESULTS / f"{name}.json").exists()


MAX_HOURS = 24.0


def fidelity_status(st: E.Setup) -> tuple[bool, str]:
    """The MT5 fidelity check (definition t2) re-judged NOW from its raw tester data with the current model."""
    from robustlab.studya import fidelity as fd

    path = RESULTS / "fidelity_t1.json"
    if not path.exists():
        return False, "no fidelity_t1.json (the MT5 fidelity check has not been run / attached)"
    rep = fd.check(st, json.loads(path.read_text(encoding="utf-8")))
    if not rep["match"]:
        bad = [t["name"] for t in rep["tests"] if not t.get("match")]
        return False, f"fidelity {fd.DEFINITION} NOT MATCHED (tests {bad}, settings {rep['account_issues'][:3]})"
    return True, f"fidelity {fd.DEFINITION} matched ({rep['n_tests']} tests + settings)"


def calibration_status(st: E.Setup) -> tuple[bool, str, str | None]:
    path = RESULTS / "calibration.json"
    if not path.exists():
        return False, "no calibration.json", None
    text = path.read_text(encoding="utf-8")
    c = json.loads(text)
    if c.get("version") != C.CALIBRATION_VERSION or c.get("config_hash") != st.cfg_hash:
        return False, f"calibration.json is {c.get('version')} / another study_a.yaml", None
    return True, "calibration fixed", ids.sha256_hex(text)


def price_check_status(st: E.Setup, calib_hash: str | None) -> tuple[bool, str]:
    path = RESULTS / "price_model_check.json"
    if not path.exists():
        return False, "no price_model_check.json"
    c = json.loads(path.read_text(encoding="utf-8"))
    if not c.get("ok") or c.get("config_hash") != st.cfg_hash or c.get("calibration_hash") != calib_hash:
        return False, f"price check not passed for this config / calibration ({c.get('issues')})"
    return True, "price model check passed (incl. P paths at the calibrated scales)"


def gates(st: E.Setup) -> list[tuple[str, bool, str]]:
    """What must hold before a main measurement (SPEC §12.6): fidelity -> calibration -> price check."""
    f_ok, f_msg = fidelity_status(st)
    c_ok, c_msg, c_hash = calibration_status(st)
    p_ok, p_msg = price_check_status(st, c_hash)
    return [("fidelity", f_ok, f_msg), ("calibration", c_ok, c_msg), ("price_check", p_ok, p_msg)]


def projected_hours(st: E.Setup, names: list[str], workers: int) -> float | None:
    """Wall-time projection from the pilot (seconds per round per worker, H0 and edge separately)."""
    per = {}
    for key, edge in (("pilot_h0", False), ("pilot_edge", True)):
        p = RESULTS / f"{key}.json"
        if not p.exists():
            return None
        s = json.loads(p.read_text(encoding="utf-8"))
        per[edge] = s["timing"]["wall_seconds"] * s["timing"]["workers"] / s["rounds"]
    sec = sum(per[st.cfg["measurements"][n].get("edge") is not None] * int(st.cfg["measurements"][n]["rounds"])
              for n in names)
    return sec / workers / 3600


def begin(st: E.Setup, name: str, rerun_reason: str | None, workers: int) -> dict[str, Any]:
    """Started marker (SPEC §12.6): a measurement that started and left no result failed technically; it is re-run
    only with the SAME config / calibration / seeds and a recorded reason. A finished one never runs again."""
    if already(name):
        raise FileExistsError(f"{name}: ALREADY_MEASURED (each measurement runs once)")
    marker = RESULTS / f"{name}.started.json"
    attempts = json.loads(marker.read_text(encoding="utf-8"))["attempts"] if marker.exists() else []
    if attempts and not rerun_reason:
        raise RuntimeError(f"{name} was started before ({attempts[-1]['started']}) and left no result: re-run only "
                           "with --rerun-reason '<what failed>' (same settings and seeds)")
    _ok, _msg, c_hash = calibration_status(st)
    if attempts and any(a["config_hash"] != st.cfg_hash or a["calibration_hash"] != c_hash for a in attempts):
        raise RuntimeError(f"{name}: config or calibration changed since the first attempt; a re-run must use the "
                           "same settings")
    attempts.append({"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "config_hash": st.cfg_hash,
                     "calibration_hash": c_hash, "measurement": st.cfg["measurements"][name], "workers": workers,
                     "version": VERSION, "rerun_reason": rerun_reason})
    RESULTS.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"name": name, "attempts": attempts}, indent=1, sort_keys=True), encoding="utf-8")
    return {"attempts": attempts}


def calibration(cfg_path: Path, progress=None, out_path: Path | None = None) -> tuple[dict[str, Any], str]:
    """Official calibration (results/calibration.json) only after the fidelity check matched (SPEC §12.6).
    out_path: a provisional copy elsewhere (not usable by a measurement)."""
    st = E.setup(cfg_path)
    if out_path is None:
        ok, msg = fidelity_status(st)
        if not ok:
            raise RuntimeError(f"official calibration refused: {msg}")
    out = C.calibrate(st, progress)
    if out_path is not None:
        out["provisional"] = True
    h = C.save(out, out_path or RESULTS / "calibration.json")
    return out, h
