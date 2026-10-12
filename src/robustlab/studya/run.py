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
    if m.get("edge") is not None:
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


def calibration(cfg_path: Path, progress=None) -> tuple[dict[str, Any], str]:
    st = E.setup(cfg_path)
    out = C.calibrate(st, progress)
    h = C.save(out, RESULTS / "calibration.json")
    return out, h
