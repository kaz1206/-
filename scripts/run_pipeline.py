"""Run P1 -> P7 for one pre-registered study with the existing rlab commands, and save every output to one file.

Usage (Windows):  .\\.venv\\Scripts\\python.exe scripts\\run_pipeline.py configs\\pipelines\\<a>.yaml [<b>.yaml ...]

Several pipeline files run one after another into ONE output file (a stop ends only that pipeline).

A thin wrapper around the public CLI: it adds no research logic, thresholds or decisions. Each stage is run once
normally (its text output is saved), then once more with --json only to read the ID for the next stage (the same
content is ALREADY_DONE, so nothing is run or recorded twice). It stops at the first problem instead of guessing:
a failed command, a P1 single test that did not succeed or has warnings, or an optimization that did not succeed.
It never runs P8a, P8b or P9 (those need a decision after P7). Log bodies are never printed by rlab, so the output
file holds no account number or IP address; it is still written outside Git (runs/ is ignored).

Pipeline file:
    name: trend_ma_h4_v2
    request: configs/requests/trend_ma_h4_v2_single.yaml   # P1 single test
    study: configs/studies/trend_ma_h4_v2.yaml             # P3 optimization
    profile: swing_trend                                   # P4 profile
    seed: 20261016                                         # P6 bootstrap seed
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

OK_RUN = {"SUCCEEDED", "ALREADY_SUCCEEDED"}


class Stop(Exception):
    pass


def rlab_exe() -> str:
    folder = Path(sys.executable).parent
    for name in ("rlab.exe", "rlab"):
        if (folder / name).exists():
            return str(folder / name)
    raise SystemExit(f"rlab not found next to {sys.executable}; run this with the project's .venv Python")


class Runner:
    def __init__(self, out_path: Path, common: list[str]):
        self.exe = rlab_exe()
        self.common = common
        self.out = out_path.open("w", encoding="utf-8")
        self.env = dict(os.environ, PYTHONIOENCODING="utf-8")

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.out.write(text + "\n")
        self.out.flush()

    def call(self, args: list[str], show: bool = True) -> tuple[int, str]:
        cmd = [self.exe, *args, *self.common]
        if show:
            self.log(f"\n$ rlab {' '.join(args)}")
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.env)
        text = (p.stdout + (p.stderr if p.stderr.strip() else "")).rstrip()
        if show:
            self.log(text)
            if p.returncode:
                self.log(f"(exit code {p.returncode})")
        return p.returncode, p.stdout

    def run(self, args: list[str]) -> None:
        code, _ = self.call(args)
        if code:
            raise Stop(f"rlab {args[0]} {args[1] if len(args) > 1 else ''} failed (exit code {code})")

    def json(self, args: list[str]) -> dict:
        code, out = self.call([*args, "--json"], show=False)
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise Stop(f"could not read the --json output of rlab {' '.join(args)}: {e}") from e
        if code:
            raise Stop(f"rlab {' '.join(args)} --json failed (exit code {code}): {data.get('message')}")
        return data


def run_one(r: Runner, path: Path, mt5: list[str]) -> str:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [k for k in ("name", "request", "study", "profile", "seed") if k not in spec]
    r.log(f"\n# ===== pipeline {spec.get('name')} ({path}) =====")
    if missing:
        r.log(f"# STOPPED: {path} is missing {missing}")
        return "STOPPED"
    r.log(f"# request={spec['request']} study={spec['study']} profile={spec['profile']} seed={spec['seed']}")
    try:
        r.log("\n## P1 single test")
        r.run(["backtest", "run", spec["request"], *mt5])
        p1 = r.json(["backtest", "run", spec["request"], *mt5])
        if p1.get("status") not in OK_RUN:
            raise Stop(f"P1 status {p1.get('status')}")
        # the repeated call only says ALREADY_SUCCEEDED; the stored job holds the warnings
        jobs = [j for j in r.json(["backtest", "show", p1["run_id"]])["jobs"] if j["status"] == "SUCCEEDED"]
        if not jobs:
            raise Stop("P1 has no SUCCEEDED job")
        if jobs[-1]["warnings"]:
            raise Stop(f"P1 has warnings {jobs[-1]['warnings']} (stop and look before optimizing)")
        r.run(["metrics", "show", p1["run_id"]])

        r.log("\n## P3 optimization")
        r.run(["optimize", "run", spec["study"], *mt5])
        p3 = r.json(["optimize", "run", spec["study"], *mt5])
        if p3.get("status") not in OK_RUN:
            raise Stop(f"P3 status {p3.get('status')}")
        rid = p3["round_id"]
        r.run(["optimize", "show", rid])

        r.log("\n## P4 filter")
        r.run(["filter", "run", rid, "--profile", spec["profile"]])
        fl = r.json(["filter", "run", rid, "--profile", spec["profile"]])["filter_id"]

        r.log("\n## P5 robustness")
        r.run(["robust", "run", rid, "--filter", fl])
        rb = r.json(["robust", "run", rid, "--filter", fl])["robust_id"]
        r.run(["robust", "map", rb, "--metric", "score"])
        r.run(["robust", "map", rb, "--metric", "robust"])

        r.log("\n## P6 statistics")
        seed = str(spec["seed"])
        r.run(["stats", "run", rid, "--filter", fl, "--seed", seed])
        st = r.json(["stats", "run", rid, "--filter", fl, "--seed", seed])["stat_id"]

        r.log("\n## P7 selection")
        r.run(["select", "run", rid, "--filter", fl, "--robust", rb, "--stats", st])
        sl = r.json(["select", "run", rid, "--filter", fl, "--robust", rb, "--stats", st])
        r.log(f"\n# ids ({spec['name']}): round={rid} filter={fl} robust={rb} stats={st} selection={sl['sel_id']} "
              f"selected={sl.get('selected_count')}")
        r.log("# P8a / P8b / P9 are NOT run by this script (they need a decision after P7).")
        return "DONE"
    except Stop as e:
        r.log(f"\n# STOPPED ({spec['name']}): {e}")
        return "STOPPED"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pipelines", type=Path, nargs="+")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--terminal", default=None, help="only for P1/P3 (default: configs/terminal.yaml)")
    ap.add_argument("--partition", default=None, help="only for P1/P3 (default: configs/data_partition.yaml)")
    ap.add_argument("--out-dir", type=Path, default=Path("runs"))
    a = ap.parse_args()

    common = ["--workspace", a.workspace] if a.workspace else []
    mt5 = (["--terminal", a.terminal] if a.terminal else []) + (["--partition", a.partition] if a.partition else [])
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    label = a.pipelines[0].stem if len(a.pipelines) == 1 else f"batch{len(a.pipelines)}"
    out_path = a.out_dir / f"{label}_{stamp}.txt"
    r = Runner(out_path, common)
    r.log(f"# run_pipeline started {stamp}: {', '.join(str(p) for p in a.pipelines)}")
    results = [(p.stem, run_one(r, p, mt5)) for p in a.pipelines]
    r.log("\n# summary: " + ", ".join(f"{n}={s}" for n, s in results))
    r.log(f"# output saved to {out_path}")
    r.out.close()
    return 0 if all(s == "DONE" for _, s in results) else 2


if __name__ == "__main__":
    sys.exit(main())
