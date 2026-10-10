"""P3 optimization flow: full-grid MT5 optimization, all passes ingested, trials ledgered (P3_PLAN §2-§3).

Success of one chunk (one MT5 optimization job) needs ALL of (ARCHITECTURE §14.4, C46-C49):
  1. the terminal started and exited on its own (LiveUpdate handoff handled, C35/C36)
  2. the tester log says "optimization finished, total passes N" with N = expected, and no cache hit
  3. the tester log shows remote 0 and cloud 0 tasks (C47)
  4. the frame-mode completion marker, one frame per expected parameter set, no duplicates
  5. per-pass integrity checks (deposit, period coverage, daily series)
  6. the XML report has the same passes with the same profit and trade count (by column names)
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from robustlab.config.loader import (
    ConfigError,
    LoadedStudy,
    load_partition,
    load_study,
    load_terminal_config,
)
from robustlab.core import ids
from robustlab.core.models import JobStatus, TerminalConfig
from robustlab.generation.grid import Chunk, Grid, GridError, build_grid, match_combo
from robustlab.metrics.pass_metrics import PASS_METRIC_DEF_VERSION, compute_pass
from robustlab.mt5 import ini_builder, optimization_io, terminal
from robustlab.oos import holdout_guard
from robustlab.single_backtest import (
    EXIT_GUARD,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USER,
    Outcome,
    Workspace,
    _sha_file,
    sweep_abandoned,
)
from robustlab.storage.db import Database, now_iso

OPT_SOURCE = "OPT_PASS"
DAILY_SCHEMA_VERSION = "opt_daily_v1"
MAX_LISTED = 20

# error classes, most severe first (the first one found becomes the job's error_class)
SEVERITY = ("AGENT_POLICY_VIOLATION", "CACHE_HIT", "FAILED_TO_START", "TIMED_OUT", "TELEMETRY_MISSING",
            "OPT_NOT_FINISHED", "MISSING_PASSES", "FRAMES_INVALID", "PASS_CHECK", "XML_UNREADABLE", "XML_MISMATCH")


@dataclass
class _Problems:
    items: list[tuple[str, str]]

    def add(self, code: str, detail: str) -> None:
        self.items.append((code, detail))

    def codes(self) -> set[str]:
        return {c for c, _ in self.items}

    def primary(self) -> str | None:
        found = self.codes()
        return next((c for c in SEVERITY if c in found), None)

    def text(self) -> str:
        return "; ".join(f"{c}: {d}" for c, d in self.items)


def _status_for(problems: _Problems) -> JobStatus:
    codes = problems.codes()
    if not codes:
        return JobStatus.SUCCEEDED
    if "FAILED_TO_START" in codes:
        return JobStatus.FAILED_TO_START
    if "TIMED_OUT" in codes:
        return JobStatus.TIMED_OUT
    if codes & {"AGENT_POLICY_VIOLATION", "CACHE_HIT"}:
        return JobStatus.QUARANTINED
    if "TELEMETRY_MISSING" in codes:
        return JobStatus.TELEMETRY_MISSING
    if codes <= {"MISSING_PASSES", "OPT_NOT_FINISHED", "XML_MISMATCH"} and "MISSING_PASSES" in codes:
        return JobStatus.PARTIAL
    return JobStatus.QUARANTINED


def expert_cache_files(data_dir: Path, expert: str) -> list[Path]:
    """The optimization cache files of this EA: Tester/cache/<EA name>.<...>.opt (X6)."""
    name = expert.replace("\\", "/").rsplit("/", 1)[-1]
    cache = data_dir / "Tester" / "cache"
    return sorted(cache.glob(f"{name}.*.opt")) if cache.is_dir() else []


def canonical_daily_bytes(grid_axes: list[str], rows: list[tuple[tuple, dict[str, str]]]) -> bytes:
    """Daily equity of all passes keyed by parameter values (independent of MT5 pass numbering)."""
    cols = ["date", "balance_close", "equity_close", "equity_min", "equity_max", "positions_max", "ticks"]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([*grid_axes, *cols])
    for combo, r in sorted(rows, key=lambda x: (tuple(float(v) for v in x[0]), x[1]["date"])):
        w.writerow([*(str(v) for v in combo), *(r[c].strip() for c in cols)])
    return buf.getvalue().encode("utf-8")


def daily_parquet_bytes(grid_axes: list[str], canonical: bytes, content_hash: str) -> bytes:
    reader = csv.DictReader(io.StringIO(canonical.decode("utf-8")))
    rows = list(reader)
    data: dict[str, Any] = {a: pa.array([float(r[a]) for r in rows], pa.float64()) for a in grid_axes}
    data["date"] = pa.array([r["date"] for r in rows], pa.string())
    for c in ("balance_close", "equity_close", "equity_min", "equity_max"):
        data[c] = pa.array([float(r[c]) for r in rows], pa.float64())
    for c in ("positions_max", "ticks"):
        data[c] = pa.array([int(r[c]) for r in rows], pa.int64())
    table = pa.table(data).replace_schema_metadata(
        {"robustlab.schema": DAILY_SCHEMA_VERSION, "robustlab.content_sha256": content_hash})
    out = io.BytesIO()
    pq.write_table(table, out)
    return out.getvalue()


def _preflight(cfg: TerminalConfig, ls: LoadedStudy) -> tuple[list[str], Path, Path | None]:
    problems = []
    data_dir = cfg.resolved_data_dir
    ex5 = data_dir / "MQL5" / "Experts" / (ls.strategy.expert.replace("\\", "/") + ".ex5")
    mq5 = (ls.strategy_path.parent / ls.strategy.mq5_path).resolve() if ls.strategy.mq5_path else None
    if ls.strategy.telemetry_version != str(optimization_io.FRAME_FORMAT):
        problems.append(f"optimization needs telemetry_version '{optimization_io.FRAME_FORMAT}' "
                        f"(the strategy declares {ls.strategy.telemetry_version!r})")
    if not cfg.terminal_path.is_file():
        problems.append(f"terminal not found: {cfg.terminal_path}")
    if not ex5.is_file():
        problems.append(f"compiled EA not found: {ex5} (copy mql5/ into the terminal and compile)")
    if mq5 is not None and not mq5.is_file():
        problems.append(f"EA source not found: {mq5}")
    if not cfg.common_files_dir.is_dir():
        problems.append(f"common files folder not found: {cfg.common_files_dir}")
    if cfg.terminal_path.is_file():
        running = terminal.find_running(cfg.terminal_path)
        if running:
            problems.append(f"the terminal is already running (pid {running}); close it first")
    return problems, ex5, mq5


def run_optimization(study_path: Path, terminal_config_path: Path, partition_path: Path, workspace_root: Path,
                     *, rerun: bool = False) -> Outcome:
    try:
        cfg = load_terminal_config(terminal_config_path)
        partition, partition_hash = load_partition(partition_path)
        ls = load_study(study_path)
        grid = build_grid(ls.study, ls.strategy)
    except (ConfigError, GridError) as e:
        return Outcome(EXIT_USER, "CONFIG_INVALID", str(e))
    ws = Workspace(workspace_root)
    try:
        return _run(ws, cfg, partition, partition_hash, ls, grid, rerun)
    finally:
        ws.close()


def _register(db: Database, ls: LoadedStudy, grid: Grid, ex5: Path, mq5: Path | None, partition_hash: str,
              terminal_path: Path) -> tuple[str, str, dict, str]:
    st, sd = ls.strategy, ls.study
    ex5_sha = _sha_file(ex5)
    mq5_sha = _sha_file(mq5) if mq5 else None
    sv = ids.strategy_version_id(ex5_sha, mq5_sha, st.telemetry_version)
    settings = sd.tester.model_dump(mode="json") | {"terminal_path": str(terminal_path), "optimization": "FULL_GRID"}
    settings_hash = ids.tester_settings_hash(settings)
    space = [{"name": a.name, "type": a.type.value, "values": list(a.values)} for a in grid.axes]
    round_id = ids.content_id("or", {"study_hash": ls.study_hash, "strategy_version_id": sv,
                                     "tester_settings_hash": settings_hash, "param_space": space,
                                     "fixed": grid.fixed})
    now = now_iso()
    with db.tx():
        db.insert("strategy_version", {
            "strategy_version_id": sv, "strategy_name": st.strategy_name, "expert": st.expert,
            "ex5_sha256": ex5_sha, "mq5_sha256": mq5_sha, "source_available": int(mq5 is not None),
            "telemetry_version": st.telemetry_version,
            "inputs_json": json.dumps([i.model_dump(mode="json") for i in st.inputs]), "registered_at": now,
        }, if_absent=True)
        db.insert("optimization_round", {
            "round_id": round_id, "study_id": sd.study_id, "study_hash": ls.study_hash,
            "strategy_version_id": sv, "strategy_family": sd.strategy_family or st.strategy_name,
            "symbol": sd.symbol, "timeframe": sd.timeframe, "from_date": sd.from_date.isoformat(),
            "to_date": sd.to_date.isoformat(), "period_semantics": "HALF_OPEN_SERVER_TIME",
            "param_space_json": ids.canonical_json(space), "fixed_json": ids.canonical_json(grid.fixed),
            "tester_settings_json": ids.canonical_json(settings), "tester_settings_hash": settings_hash,
            "partition_hash": partition_hash, "algorithm": "FULL_GRID", "expected_passes": grid.total,
            "chunk_count": len(grid.chunks), "created_at": now,
        }, if_absent=True)
    return sv, round_id, settings, settings_hash


def _run(ws: Workspace, cfg: TerminalConfig, partition, partition_hash: str, ls: LoadedStudy, grid: Grid,
         rerun: bool) -> Outcome:
    db, sd = ws.db, ls.study
    sweep_abandoned(db, "opt_job")
    problems, ex5, mq5 = _preflight(cfg, ls)
    if problems:
        return Outcome(EXIT_USER, "PREFLIGHT_FAILED", "; ".join(problems))
    decision = holdout_guard.evaluate(db, request_hash=ls.study_hash, from_date=sd.from_date, to_date=sd.to_date,
                                      partition=partition, partition_hash=partition_hash)
    if not decision.approved:
        return Outcome(EXIT_GUARD, "GUARD_REJECTED", decision.reason)
    sv, round_id, settings, settings_hash = _register(db, ls, grid, ex5, mq5, partition_hash, cfg.terminal_path)

    todo = []
    for ch in grid.chunks:
        done = db.one("SELECT job_id FROM opt_job WHERE round_id = ? AND chunk_index = ? AND status = 'SUCCEEDED' "
                      "ORDER BY job_id DESC LIMIT 1", (round_id, ch.index))
        if rerun or done is None:
            todo.append(ch)
    details: dict[str, Any] = {"round_id": round_id, "expected_passes": grid.total, "chunks": len(grid.chunks),
                               "jobs": []}
    if not todo:
        return Outcome(EXIT_OK, "ALREADY_SUCCEEDED", "every chunk already has a successful job (use --rerun to repeat)",
                       details=details)

    for ch in todo:
        job_id = ids.new_job_id()
        attempt = db.one("SELECT COUNT(*) AS n FROM opt_job WHERE round_id = ? AND chunk_index = ?",
                         (round_id, ch.index))["n"] + 1
        spec = {"round_id": round_id, "chunk_index": ch.index, "strategy_version_id": sv, "expert": ls.strategy.expert,
                "symbol": sd.symbol, "timeframe": sd.timeframe, "from_date": sd.from_date.isoformat(),
                "to_date": sd.to_date.isoformat(), "axes": [{"name": a.name, "start": a.start, "step": a.step,
                                                             "stop": a.stop} for a in ch.axes],
                "fixed": grid.fixed, "tester": settings}
        with db.tx():
            db.insert("opt_job", {
                "job_id": job_id, "round_id": round_id, "chunk_index": ch.index,
                "spec_hash": ids.sha256_hex(ids.canonical_json(spec)), "spec_json": ids.canonical_json(spec),
                "attempt_no": attempt, "status": JobStatus.PREPARING.value, "terminal_path": str(cfg.terminal_path),
                "created_at": now_iso(), "expected_passes": ch.passes,
            })
            # C44: every requested pass is a trial; re-running the same chunk adds none
            db.insert("trial_ledger", {
                "round_id": round_id, "chunk_index": ch.index, "strategy_family": sd.strategy_family or
                ls.strategy.strategy_name, "study_id": sd.study_id, "first_job_id": job_id,
                "n_trials_raw": ch.passes, "n_trials_effective": None, "method": "RAW", "created_at": now_iso(),
            }, if_absent=True)
        try:
            res = _execute_chunk(ws, cfg, ls, grid, ch, job_id, sv, round_id, settings_hash)
        except Exception as e:  # never leave a job RUNNING, never treat it as a success
            db.update_job(job_id, table="opt_job", status=JobStatus.INTERNAL_ERROR.value, finished_at=now_iso(),
                          error_class=JobStatus.INTERNAL_ERROR.value, error_detail=f"{type(e).__name__}: {e}")
            res = {"job_id": job_id, "chunk": ch.index, "status": JobStatus.INTERNAL_ERROR.value,
                   "message": f"{type(e).__name__}: {e}"}
        details["jobs"].append(res)
        if res["status"] != JobStatus.SUCCEEDED.value:
            return Outcome(EXIT_RUNTIME, res["status"], res.get("message", ""), job_id=job_id, details=details)
    return Outcome(EXIT_OK, JobStatus.SUCCEEDED.value, "", job_id=details["jobs"][-1]["job_id"], details=details)


def _execute_chunk(ws: Workspace, cfg: TerminalConfig, ls: LoadedStudy, grid: Grid, ch: Chunk, job_id: str,
                   sv: str, round_id: str, settings_hash: str) -> dict[str, Any]:
    db, store, sd, st = ws.db, ws.store, ls.study, ls.strategy
    job_dir = ws.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    data_dir = cfg.resolved_data_dir

    set_name = f"rl_{job_id}.set"
    set_bytes = ini_builder.encode_set(ini_builder.build_opt_set_text(st.inputs, grid.fixed, ch.axes, job_id))
    set_path = data_dir / "MQL5" / "Profiles" / "Tester" / set_name
    set_path.parent.mkdir(parents=True, exist_ok=True)
    set_path.write_bytes(set_bytes)
    ini_text = ini_builder.build_ini_text(
        expert=st.expert, set_filename=set_name, symbol=sd.symbol, timeframe=sd.timeframe,
        from_date=sd.from_date, to_date=sd.to_date, tester=sd.tester, job_id=job_id, optimization=True,
    )
    ini_path = job_dir / "job.ini"
    ini_path.write_bytes(ini_text.encode("ascii"))

    # C48: a cached optimization runs no pass and sends no frame, so the EA's cache goes first
    deleted = []
    for f in expert_cache_files(data_dir, st.expert):
        f.unlink()
        deleted.append(f.name)

    db.update_job(job_id, table="opt_job", status=JobStatus.RUNNING.value, started_at=now_iso(),
                  cache_deleted_json=deleted)
    outcome = terminal.run_terminal(
        cfg.terminal_path, data_dir, ini_path, start_timeout_sec=cfg.start_timeout_sec,
        run_timeout_sec=cfg.run_timeout_sec, poll_interval_sec=cfg.poll_interval_sec,
        on_launch=lambda pid: db.update_job(job_id, table="opt_job", pid=pid),
    )

    pr = _Problems([])
    warnings: list[str] = []
    if outcome.updated:
        warnings.append(f"TERMINAL_UPDATED: builds {' -> '.join(str(b) for b in outcome.builds) or 'unknown'}")
    tester_dir = data_dir / "Tester" / "logs"
    tester_text = "".join(terminal.decode_log(b) for p, b in outcome.log_segments.items() if p.parent == tester_dir)
    facts = optimization_io.read_tester_log(tester_text)
    frame_files = optimization_io.locate_frames(cfg.common_files_dir, job_id)
    report_files = sorted(data_dir.glob(f"{ini_builder.report_name(job_id)}*"))
    frames: optimization_io.FrameData | None = None
    matched: dict[tuple, optimization_io.PassFrame] = {}
    xml_rows: dict[tuple, dict[str, str]] = {}

    if outcome.kind is terminal.TerminalOutcomeKind.FAILED_TO_START:
        pr.add("FAILED_TO_START", f"the terminal log never showed {terminal.START_PATTERN!r}")
    elif outcome.kind is terminal.TerminalOutcomeKind.TIMED_OUT:
        pr.add("TIMED_OUT", f"no result within {cfg.run_timeout_sec} s")
    else:
        _check_tester_log(facts, ch, pr)
        frames = _check_frames(frame_files, job_id, ch, sd, pr, matched)
        _check_xml(report_files, job_id, ch, matched, pr, xml_rows)

    status = _status_for(pr)
    roles: list[tuple[str, str, str]] = [
        ("ini", "job.ini", store.put_bytes(ini_text.encode("ascii"))),
        ("set", set_name, store.put_bytes(set_bytes)),
    ]
    for role, path in frame_files.items():
        if path.is_file():
            roles.append((f"frames_{role}", path.name, store.put_file(path)))
    for path in report_files:
        roles.append(("report", path.name, store.put_file(path)))
    for path, data in outcome.log_segments.items():  # F8: contains login and IP; stored, never printed
        roles.append(("log_segment", str(path.relative_to(data_dir)).replace("\\", "/"), store.put_bytes(data)))

    fields: dict[str, Any] = {
        "status": status.value, "finished_at": now_iso(), "exit_code": outcome.exit_code,
        "error_class": pr.primary(), "error_detail": pr.text() or None,
        "received_passes": len(frames.passes) if frames else None,
        "tester_log_json": {"started": facts.started, "finished_passes": facts.finished_passes,
                            "cache_hit": facts.cache_hit, "local": facts.local_tasks,
                            "remote": facts.remote_tasks, "cloud": facts.cloud_tasks},
    }
    if frames:
        fields["observed_build"] = frames.init.get("terminal_build")
    axis_names = [a.name for a in ch.axes]
    pass_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    if status is JobStatus.SUCCEEDED and frames:
        canonical = canonical_daily_bytes(axis_names, [(c, r) for c, pf in matched.items() for r in pf.daily])
        content_hash = ids.sha256_hex(canonical)
        roles.append(("daily_canonical_csv", "daily.csv", store.put_bytes(canonical)))
        roles.append(("daily_parquet", "daily.parquet", store.put_bytes(daily_parquet_bytes(axis_names, canonical,
                                                                                            content_hash))))
        fields["daily_content_hash"] = content_hash
        warnings += _repro_warnings(db, round_id, ch.index, job_id, fields.get("observed_build"), content_hash)
        now = now_iso()
        rnd = db.one("SELECT tester_settings_json, partition_hash FROM optimization_round WHERE round_id = ?",
                     (round_id,))
        for combo, pf in sorted(matched.items(), key=lambda kv: kv[1].pass_no):
            params = grid.params_for(combo)
            cd = ids.candidate_id(sv, sd.symbol, sd.timeframe, params)
            rn = ids.run_id(cd, sd.from_date, sd.to_date, settings_hash, source=OPT_SOURCE)
            m, na = compute_pass(pf.stats, pf.tracking, pf.daily, sd.tester.deposit)
            candidates.append({"candidate_id": cd, "strategy_version_id": sv, "symbol": sd.symbol,
                               "timeframe": sd.timeframe, "params_json": ids.canonical_json(params),
                               "origin": "OPTIMIZATION", "created_at": now})
            runs.append({"run_id": rn, "candidate_id": cd, "from_date": sd.from_date.isoformat(),
                         "to_date": sd.to_date.isoformat(), "period_semantics": "HALF_OPEN_SERVER_TIME",
                         "tester_settings_json": rnd["tester_settings_json"], "tester_settings_hash": settings_hash,
                         "partition_hash": rnd["partition_hash"],
                         "cost_scenario": "BASE", "source": OPT_SOURCE, "created_at": now})
            x = xml_rows[combo]
            pass_rows.append({"job_id": job_id, "pass_no": pf.pass_no, "run_id": rn, "candidate_id": cd,
                              "metric_def_version": PASS_METRIC_DEF_VERSION,
                              "metrics_json": json.dumps(m, sort_keys=True),
                              "unavailable_json": json.dumps(na, sort_keys=True),
                              "mt5_stats_json": json.dumps(pf.stats, sort_keys=True),
                              "xml_profit": x["Profit"], "xml_trades": int(float(x["Trades"])),
                              "daily_rows": len(pf.daily)})
    fields["warnings_json"] = warnings

    with db.tx():
        for role, name, sha in roles:
            db.insert("opt_job_artifact", {"job_id": job_id, "role": role, "name": name, "sha256": sha})
        for row in candidates:
            db.insert("candidate", row, if_absent=True)
        for row in runs:
            db.insert("run", row, if_absent=True)
        for row in pass_rows:
            db.insert("pass_result", row)
        db.update_job(job_id, table="opt_job", **fields)

    for path in [set_path, *[p for p in frame_files.values() if p.is_file()], *report_files]:
        path.unlink(missing_ok=True)
    return {"job_id": job_id, "chunk": ch.index, "status": status.value, "message": pr.text(),
            "error_class": pr.primary(), "expected": ch.passes, "received": fields["received_passes"],
            "observed_build": fields.get("observed_build"), "cache_deleted": deleted, "warnings": warnings,
            "terminal_seconds": outcome.seconds}


def _check_tester_log(facts: optimization_io.TesterLogFacts, ch: Chunk, pr: _Problems) -> None:
    if facts.cache_hit:
        pr.add("CACHE_HIT", "the tester log says 'optimization already processed': results came from the cache")
    if facts.local_tasks is None:
        if not facts.cache_hit:  # a cache hit runs no pass and logs no breakdown (O2 in S-HW-P3)
            pr.add("AGENT_POLICY_VIOLATION", "the tester log has no 'local/remote/cloud tasks' line")
    elif facts.remote_tasks or facts.cloud_tasks:
        pr.add("AGENT_POLICY_VIOLATION",
               f"passes ran outside this PC: remote {facts.remote_tasks}, cloud {facts.cloud_tasks}")
    if facts.finished_passes != ch.passes:
        pr.add("OPT_NOT_FINISHED", f"tester log total passes {facts.finished_passes} != expected {ch.passes}")


def _check_frames(files: dict[str, Path], job_id: str, ch: Chunk, sd, pr: _Problems,
                  matched: dict[tuple, optimization_io.PassFrame]) -> optimization_io.FrameData | None:
    if not files["fm_done"].is_file():
        pr.add("TELEMETRY_MISSING", "frame completion marker not found")
        return None
    try:
        frames = optimization_io.read_frames(files, job_id)
    except optimization_io.FrameError as e:
        pr.add("FRAMES_INVALID", str(e))
        return None
    done = frames.done
    if int(done.get("frames_received", -1)) != len(frames.passes):
        pr.add("FRAMES_INVALID", f"marker says {done.get('frames_received')} frames, file has {len(frames.passes)}")
    counters = {k: int(done.get(k, -1)) for k in ("duplicate_passes", "bad_frames", "write_failures")}
    if any(v != 0 for v in counters.values()):
        pr.add("FRAMES_INVALID", f"frame collector reported {counters}")
    unmatched = []
    for pf in frames.passes:
        combo = match_combo(ch, pf.inputs)
        if combo is None:
            unmatched.append(pf.pass_no)
        elif combo in matched:
            pr.add("FRAMES_INVALID", f"parameter set {combo} received twice (passes {matched[combo].pass_no}, "
                                     f"{pf.pass_no})")
        else:
            matched[combo] = pf
    if unmatched:
        pr.add("FRAMES_INVALID", f"passes outside the requested grid: {unmatched[:MAX_LISTED]}")
    missing = [c for c in ch.combos() if c not in matched]
    if missing:
        pr.add("MISSING_PASSES", f"{len(missing)} of {ch.passes} parameter sets missing, e.g. {missing[:MAX_LISTED]}")
    bad = []
    for pf in matched.values():
        bad += optimization_io.check_pass(pf, deposit=sd.tester.deposit, from_date=sd.from_date,
                                          to_date=sd.to_date, tolerance_days=sd.coverage_tolerance_days)
    if bad:
        pr.add("PASS_CHECK", "; ".join(bad[:MAX_LISTED]) + (f" (+{len(bad) - MAX_LISTED} more)" if len(bad) > MAX_LISTED else ""))
    return frames


def _check_xml(report_files: list[Path], job_id: str, ch: Chunk, matched: dict[tuple, optimization_io.PassFrame],
               pr: _Problems, xml_rows: dict[tuple, dict[str, str]]) -> None:
    xml = [p for p in report_files if p.suffix.lower() == ".xml"]
    if not xml:
        pr.add("XML_UNREADABLE", f"optimization report {ini_builder.report_name(job_id)}.xml not found")
        return
    try:
        rows = optimization_io.read_opt_report(xml[0].read_bytes(), [a.name for a in ch.axes])
    except optimization_io.XmlReportError as e:
        pr.add("XML_UNREADABLE", str(e))
        return
    if len(rows) != ch.passes:
        pr.add("XML_MISMATCH", f"XML has {len(rows)} rows, expected {ch.passes}")
    diffs = []
    for r in rows:
        combo = match_combo(ch, r)
        if combo is None:
            diffs.append(f"XML row outside the grid: pass {r.get('Pass')}")
            continue
        xml_rows[combo] = r
        pf = matched.get(combo)
        if pf is None:
            continue  # reported as MISSING_PASSES already
        if abs(float(r["Profit"]) - round(pf.stats["profit"], 2)) > 0.005 or int(float(r["Trades"])) != int(pf.stats["trades"]):
            diffs.append(f"{combo}: XML profit {r['Profit']} trades {r['Trades']} vs frame "
                         f"{pf.stats['profit']:.2f} / {int(pf.stats['trades'])}")
    if diffs:
        pr.add("XML_MISMATCH", "; ".join(diffs[:MAX_LISTED]))
    for combo in matched:
        if combo not in xml_rows:
            pr.add("XML_MISMATCH", f"{combo} has a frame but no XML row")
            break


def _repro_warnings(db: Database, round_id: str, chunk: int, job_id: str, build: int | None,
                    content_hash: str) -> list[str]:
    prev = db.one("SELECT job_id, observed_build, daily_content_hash FROM opt_job WHERE round_id = ? AND "
                  "chunk_index = ? AND status = 'SUCCEEDED' AND job_id <> ? ORDER BY job_id DESC LIMIT 1",
                  (round_id, chunk, job_id))
    if prev is None:
        return []
    if prev["observed_build"] != build:
        return [f"REPRO_NOT_COMPARABLE_BUILD_CHANGED: {prev['job_id']} build {prev['observed_build']} -> {build}"]
    if prev["daily_content_hash"] == content_hash:
        return [f"REPRO_MATCH: daily equity of all passes identical to {prev['job_id']}"]
    return [f"REPRO_MISMATCH: daily equity differs from {prev['job_id']} with the same build"]
