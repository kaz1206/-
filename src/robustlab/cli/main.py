"""rlab CLI: backtest run/show, jobs list, metrics (P2), optimize run/show and trials show (P3).

Exit codes: 0 ok, 1 configuration/input error, 2 runtime failure, 3 holdout guard rejection.
Log contents are never printed (they contain the account login and IP address, F8).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from robustlab.single_backtest import run_backtest
from robustlab.storage.db import Database

app = typer.Typer(no_args_is_help=True, add_completion=False, help="RobustLab (MT5 backtests and optimizations)")
backtest = typer.Typer(no_args_is_help=True, help="Run and inspect single backtests")
jobs_app = typer.Typer(no_args_is_help=True, help="Inspect jobs")
app.add_typer(backtest, name="backtest")
app.add_typer(jobs_app, name="jobs")
metrics_app = typer.Typer(no_args_is_help=True, help="Compute and inspect metrics (P2)")
app.add_typer(metrics_app, name="metrics")
optimize_app = typer.Typer(no_args_is_help=True, help="Full-grid MT5 optimizations (P3)")
app.add_typer(optimize_app, name="optimize")
trials_app = typer.Typer(no_args_is_help=True, help="Trial ledger (number of tried configurations, P3)")
app.add_typer(trials_app, name="trials")
filter_app = typer.Typer(no_args_is_help=True, help="Initial filter: profiles, PSR gate, all gates recorded (P4)")
app.add_typer(filter_app, name="filter")
robust_app = typer.Typer(no_args_is_help=True, help="Robustness: neighbourhood statistics, regions (P5)")
app.add_typer(robust_app, name="robust")
oos_app = typer.Typer(no_args_is_help=True, help="P8b OOS: one manifest, one run, the holdout is burned")
app.add_typer(oos_app, name="oos")
null_app = typer.Typer(no_args_is_help=True, help="P10 null-hypothesis test: chance pass rate of the pipeline")
app.add_typer(null_app, name="null")
risk_app = typer.Typer(no_args_is_help=True, help="P9 risk and allocation (lots only for P8a passes, never rounded up)")
app.add_typer(risk_app, name="risk")
validate_app = typer.Typer(no_args_is_help=True, help="P8a validation in new periods (fixed parameters)")
app.add_typer(validate_app, name="validate")
select_app = typer.Typer(no_args_is_help=True, help="Selection: gates, region representatives, budget (P7)")
app.add_typer(select_app, name="select")
stats_app = typer.Typer(no_args_is_help=True, help="Statistics: DSR, block-bootstrap intervals (P6, in-sample)")
app.add_typer(stats_app, name="stats")

WORKSPACE = typer.Option(Path("workspace"), "--workspace", help="Workspace folder (DB, artifacts)")


def _emit(data: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    for k, v in data.items():
        if isinstance(v, list):
            typer.echo(f"{k}:")
            for item in v:
                typer.echo(f"  - {item}")
        else:
            typer.echo(f"{k}: {v}")


@backtest.command("run")
def backtest_run(
    request: Path = typer.Argument(..., exists=True, dir_okay=False, help="Request YAML"),
    terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal", help="Terminal config YAML"),
    partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition", help="Holdout definition"),
    workspace: Path = WORKSPACE,
    rerun: bool = typer.Option(False, "--rerun", help="Run again even if a successful result exists"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    out = run_backtest(request, terminal, partition, workspace, rerun=rerun)
    _emit({"status": out.status, "message": out.message, "run_id": out.run_id, "job_id": out.job_id,
           **out.details}, as_json)
    raise typer.Exit(out.exit_code)


def _open(workspace: Path) -> Database:
    path = workspace / "robustlab.sqlite"
    if not path.is_file():
        typer.echo(f"no workspace database at {path}", err=True)
        raise typer.Exit(1)
    return Database(path)


@backtest.command("show")
def backtest_show(run_id: str, workspace: Path = WORKSPACE,
                  as_json: bool = typer.Option(False, "--json")) -> None:
    db = _open(workspace)
    try:
        run = db.one("SELECT * FROM run WHERE run_id = ?", (run_id,))
        if run is None:
            typer.echo(f"unknown run {run_id}", err=True)
            raise typer.Exit(1)
        cand = db.one("SELECT * FROM candidate WHERE candidate_id = ?", (run["candidate_id"],))
        sv = db.one("SELECT * FROM strategy_version WHERE strategy_version_id = ?", (cand["strategy_version_id"],))
        jobs = []
        for j in db.all("SELECT * FROM job WHERE run_id = ? ORDER BY job_id", (run_id,)):
            arts = db.all("SELECT role, name, sha256 FROM job_artifact WHERE job_id = ? ORDER BY role, name",
                          (j["job_id"],))
            jobs.append({
                "job_id": j["job_id"], "attempt": j["attempt_no"], "status": j["status"],
                "observed_build": j["observed_build"], "deals": j["deals_count"],
                "deals_content_hash": j["deals_content_hash"], "net_profit_mt5": j["net_profit_mt5"],
                "net_profit_sum": j["net_profit_sum"], "error": j["error_detail"],
                "warnings": json.loads(j["warnings_json"]),
                "artifacts": [f"{a['role']}:{a['name']} sha256={a['sha256']}" for a in arts],
            })
        data = {
            "run_id": run_id,
            "strategy": f"{sv['strategy_name']} ({sv['strategy_version_id']}, ex5 {sv['ex5_sha256'][:12]}, "
                        f"mq5 {(sv['mq5_sha256'] or 'n/a')[:12]})",
            "candidate_id": cand["candidate_id"],
            "symbol_timeframe": f"{cand['symbol']} {cand['timeframe']}",
            "params": cand["params_json"],
            "period": f"[{run['from_date']}, {run['to_date']}) broker server time",
            "tester_settings": run["tester_settings_json"],
            "jobs": jobs,
        }
        if as_json:
            _emit(data, True)
        else:
            for k in ("run_id", "strategy", "candidate_id", "symbol_timeframe", "params", "period", "tester_settings"):
                typer.echo(f"{k}: {data[k]}")
            for j in jobs:
                typer.echo(f"job {j['job_id']} attempt={j['attempt']} status={j['status']} build={j['observed_build']} "
                           f"deals={j['deals']} hash={(j['deals_content_hash'] or '-')[:16]} "
                           f"profit(mt5/sum)={j['net_profit_mt5']}/{j['net_profit_sum']}")
                if j["error"]:
                    typer.echo(f"  error: {j['error']}")
                for w in j["warnings"]:
                    typer.echo(f"  warning: {w}")
                for a in j["artifacts"]:
                    typer.echo(f"  artifact {a}")
    finally:
        db.close()


@jobs_app.command("list")
def jobs_list(workspace: Path = WORKSPACE, status: str | None = typer.Option(None, "--status"),
              limit: int = typer.Option(20, "--limit"), as_json: bool = typer.Option(False, "--json")) -> None:
    db = _open(workspace)
    try:
        where, params = (" WHERE status = ?", (status,)) if status else ("", ())
        rows = [dict(r) | {"kind": "SINGLE"} for r in db.all(
            "SELECT job_id, run_id, attempt_no, status, observed_build, deals_count, error_class FROM job"
            + where + " ORDER BY job_id DESC LIMIT ?", (*params, limit))]
        rows += [dict(r) | {"kind": "OPT"} for r in db.all(
            "SELECT job_id, round_id, chunk_index, attempt_no, status, observed_build, expected_passes, "
            "received_passes, error_class FROM opt_job" + where + " ORDER BY job_id DESC LIMIT ?", (*params, limit))]
    finally:
        db.close()
    rows = sorted(rows, key=lambda r: r["job_id"], reverse=True)[:limit]
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    for r in rows:
        err = f" error={r['error_class']}" if r["error_class"] else ""
        if r["kind"] == "OPT":
            typer.echo(f"{r['job_id']} {r['round_id']} chunk={r['chunk_index']} #{r['attempt_no']} {r['status']} "
                       f"build={r['observed_build']} passes={r['received_passes']}/{r['expected_passes']}{err}")
        else:
            typer.echo(f"{r['job_id']} {r['run_id']} #{r['attempt_no']} {r['status']} build={r['observed_build']} "
                       f"deals={r['deals_count']}{err}")


@jobs_app.command("startlog")
def jobs_startlog(job_id: str = typer.Argument("last", help="job id, or 'last'"), workspace: Path = WORKSPACE) -> None:
    """Diagnose a start failure: known lines of the job's terminal log, MASKED (no login / IP; other lines only
    counted)."""
    from robustlab.mt5 import log_digest, terminal
    from robustlab.storage.artifacts import ArtifactStore

    db = _open(workspace)
    try:
        job = (db.one("SELECT job_id, status, exit_code, error_detail FROM job ORDER BY job_id DESC LIMIT 1")
               if job_id == "last" else
               db.one("SELECT job_id, status, exit_code, error_detail FROM job WHERE job_id = ?", (job_id,)))
        if job is None:
            typer.echo("no such job", err=True)
            raise typer.Exit(1)
        typer.echo(f"job {job['job_id']} status={job['status']} exit_code={job['exit_code']}")
        store = ArtifactStore(workspace.resolve() / "artifacts", db)
        segs = db.all("SELECT name, sha256 FROM job_artifact WHERE job_id = ? AND role = 'log_segment' ORDER BY name",
                      (job["job_id"],))
        if not segs:
            typer.echo("no terminal log was written during this job (the terminal wrote nothing after launch)")
        for seg in segs:
            shown, hidden = log_digest.digest(terminal.decode_log(store.read(seg["sha256"])))
            typer.echo(f"--- {seg['name']} ({len(shown)} known lines shown, {hidden} other lines not shown)")
            for line in shown:
                typer.echo(f"  {line}")
    finally:
        db.close()


@metrics_app.command("compute")
def metrics_compute(job_id: str | None = typer.Argument(None, help="Job to compute (omit with --all)"),
                    all_jobs: bool = typer.Option(False, "--all", help="Every SUCCEEDED job without metrics"),
                    workspace: Path = WORKSPACE) -> None:
    from robustlab.metrics import store as metrics_store
    from robustlab.storage.artifacts import ArtifactStore

    if bool(job_id) == all_jobs:
        typer.echo("give a job_id or --all", err=True)
        raise typer.Exit(1)
    db = _open(workspace)
    try:
        store = ArtifactStore(workspace.resolve() / "artifacts", db)
        ids = [job_id] if job_id else [r["job_id"] for r in db.all(
            "SELECT job_id FROM job WHERE status = 'SUCCEEDED' ORDER BY job_id")]
        failed = False
        for jid in ids:
            try:
                sm = metrics_store.compute_and_store(db, store, jid)
            except metrics_store.MetricsError as e:
                typer.echo(f"{jid}: {e}", err=True)
                failed = True
                continue
            mismatches = [c["name"] for c in sm.checks if c["status"] == "MISMATCH"]
            state = "computed" if sm.created else "already computed"
            typer.echo(f"{jid}: {state} ({sm.version})" + (f" MISMATCH: {mismatches}" if mismatches else ""))
    finally:
        db.close()
    raise typer.Exit(2 if failed else 0)


@metrics_app.command("show")
def metrics_show(run_id: str, job: str | None = typer.Option(None, "--job", help="Default: latest SUCCEEDED job"),
                 version: str | None = typer.Option(None, "--version", help="Metric definition version (default: current)"),
                 workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.metrics import store as metrics_store

    db = _open(workspace)
    try:
        if job is None:
            row = db.one("SELECT job_id FROM job WHERE run_id = ? AND status = 'SUCCEEDED' ORDER BY job_id DESC LIMIT 1",
                         (run_id,))
            if row is None:
                typer.echo(f"no SUCCEEDED job for {run_id}", err=True)
                raise typer.Exit(1)
            job = row["job_id"]
        sm = metrics_store.load(db, job, version)
        if sm is None:
            typer.echo(f"no metrics for {job}; run: rlab metrics compute {job}", err=True)
            raise typer.Exit(1)
        periods = [dict(r) for r in db.all(
            "SELECT bucket_type, bucket_key, net_profit, trades, balance_max_dd FROM period_performance "
            "WHERE job_id = ? AND metric_def_version = ? ORDER BY bucket_type DESC, bucket_key", (job, sm.version))]
    finally:
        db.close()
    if as_json:
        typer.echo(json.dumps({"job_id": job, "version": sm.version, "metrics": sm.metrics,
                               "unavailable": sm.unavailable, "checks": sm.checks, "periods": periods},
                              indent=2, ensure_ascii=False))
        return
    typer.echo(f"job: {job}  metrics: {sm.version}")
    typer.echo("-- comparison with MT5 --")
    for c in sm.checks:
        typer.echo(f"  {c['status']:<14} {c['name']:<26} ours={c['ours']} mt5={c['mt5']}")
    typer.echo("-- metrics --")
    for k in sorted(sm.metrics):
        v = sm.metrics[k]
        reason = f"  ({sm.unavailable[k]})" if v is None and k in sm.unavailable else ""
        typer.echo(f"  {k}: {v}{reason}")
    typer.echo("-- by year --")
    for p in periods:
        if p["bucket_type"] == "YEAR":
            typer.echo(f"  {p['bucket_key']}: net={p['net_profit']} trades={p['trades']} max_dd={p['balance_max_dd']}")


@metrics_app.command("runs")
def metrics_runs(job_id: str, workspace: Path = WORKSPACE) -> None:
    """Diagnostics: every longest win/loss run of a job (read-only)."""
    from robustlab.metrics import engine
    from robustlab.mt5 import telemetry_reader as tr
    from robustlab.storage.artifacts import ArtifactStore

    db = _open(workspace)
    try:
        store = ArtifactStore(workspace.resolve() / "artifacts", db)
        row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = 'telemetry_deals'", (job_id,))
        stats = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = 'telemetry_stats'", (job_id,))
        if row is None or stats is None:
            typer.echo(f"no telemetry for {job_id}", err=True)
            raise typer.Exit(1)
        deals = tr.parse_csv_text(store.read(row["sha256"]).decode("utf-8-sig"), tr.DEALS_COLUMNS, "deals")
        mt5 = json.loads(store.read(stats["sha256"]).decode("utf-8-sig")).get("values", {})
    finally:
        db.close()
    for label, runs in engine.longest_runs(deals).items():
        typer.echo(f"{label}:")
        for r in runs:
            typer.echo(f"  {r}")
    typer.echo("mt5: " + ", ".join(f"{k}={mt5.get(k)}" for k in
                                   ("max_conwins", "max_conprofit_trades", "max_conlosses", "max_conloss_trades")))


@optimize_app.command("run")
def optimize_run(
    study: Path = typer.Argument(..., exists=True, dir_okay=False, help="Study YAML"),
    terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal", help="Terminal config YAML"),
    partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition", help="Holdout definition"),
    workspace: Path = WORKSPACE,
    rerun: bool = typer.Option(False, "--rerun", help="Run every chunk again even if it already succeeded"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    from robustlab.optimization import run_optimization

    out = run_optimization(study, terminal, partition, workspace, rerun=rerun)
    if as_json:
        _emit({"status": out.status, "message": out.message, **out.details}, True)
        raise typer.Exit(out.exit_code)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    for k in ("round_id", "expected_passes", "chunks"):
        if k in out.details:
            typer.echo(f"{k}: {out.details[k]}")
    for j in out.details.get("jobs", []):
        typer.echo(f"job {j['job_id']} chunk={j['chunk']} {j['status']} passes={j.get('received')}/{j.get('expected')} "
                   f"build={j.get('observed_build')} seconds={j.get('terminal_seconds')}")
        if j.get("error_class") == "AGENT_POLICY_VIOLATION":
            typer.secho("  SAFETY: passes may have run on remote or cloud agents. Check the MT5 tester agent "
                        "settings (MQL5 Cloud Network must be off) before running again.", fg="red", err=True)
        for w in j.get("warnings", []):
            typer.echo(f"  warning: {w}")
    raise typer.Exit(out.exit_code)


@optimize_app.command("show")
def optimize_show(round_id: str, workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    """Round, jobs and a distribution summary of all passes (no ranking: selection belongs to later stages)."""
    import statistics

    db = _open(workspace)
    try:
        rnd = db.one("SELECT * FROM optimization_round WHERE round_id = ?", (round_id,))
        if rnd is None:
            typer.echo(f"unknown round {round_id}", err=True)
            raise typer.Exit(1)
        jobs = [dict(r) for r in db.all("SELECT * FROM opt_job WHERE round_id = ? ORDER BY chunk_index, job_id",
                                        (round_id,))]
        latest_ok = {}
        for j in jobs:
            if j["status"] == "SUCCEEDED":
                latest_ok[j["chunk_index"]] = j["job_id"]
        profits = []
        for jid in latest_ok.values():
            for r in db.all("SELECT metrics_json FROM pass_result WHERE job_id = ?", (jid,)):
                profits.append(json.loads(r["metrics_json"])["net_profit"])
        trials = db.one("SELECT COALESCE(SUM(n_trials_raw), 0) AS n FROM trial_ledger WHERE strategy_family = ?",
                        (rnd["strategy_family"],))["n"]
    finally:
        db.close()
    summary = {
        "passes_stored": len(profits),
        "chunks_succeeded": f"{len(latest_ok)}/{rnd['chunk_count']}",
        "net_profit_min": round(min(profits), 2) if profits else None,
        "net_profit_median": round(statistics.median(profits), 2) if profits else None,
        "net_profit_max": round(max(profits), 2) if profits else None,
        "share_profitable": round(sum(p > 0 for p in profits) / len(profits), 4) if profits else None,
    }
    data = {
        "round_id": round_id, "study_id": rnd["study_id"], "strategy_family": rnd["strategy_family"],
        "symbol_timeframe": f"{rnd['symbol']} {rnd['timeframe']}",
        "period": f"[{rnd['from_date']}, {rnd['to_date']}) broker server time",
        "param_space": rnd["param_space_json"], "fixed": rnd["fixed_json"],
        "expected_passes": rnd["expected_passes"], "summary": summary,
        "trials_in_family": trials,
        "jobs": [{k: j[k] for k in ("job_id", "chunk_index", "attempt_no", "status", "expected_passes",
                                    "received_passes", "observed_build", "daily_content_hash", "error_class",
                                    "error_detail")}
                 | {"warnings": json.loads(j["warnings_json"]), "cache_deleted": json.loads(j["cache_deleted_json"] or "[]"),
                    "tester_log": json.loads(j["tester_log_json"] or "{}")} for j in jobs],
    }
    if as_json:
        _emit(data, True)
        return
    for k in ("round_id", "study_id", "strategy_family", "symbol_timeframe", "period", "param_space", "fixed",
              "expected_passes", "trials_in_family"):
        typer.echo(f"{k}: {data[k]}")
    typer.echo("summary: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    for j in data["jobs"]:
        typer.echo(f"job {j['job_id']} chunk={j['chunk_index']} #{j['attempt_no']} {j['status']} "
                   f"passes={j['received_passes']}/{j['expected_passes']} build={j['observed_build']} "
                   f"daily_hash={(j['daily_content_hash'] or '-')[:16]} agents={j['tester_log']}")
        if j["error_detail"]:
            typer.echo(f"  error: {j['error_class']}: {j['error_detail']}")
        for w in j["warnings"]:
            typer.echo(f"  warning: {w}")


@optimize_app.command("pass")
def optimize_pass(round_id: str, candidate_id: str, workspace: Path = WORKSPACE) -> None:
    """One pass of a round, by candidate_id (e.g. to compare with a single test of the same parameters)."""
    db = _open(workspace)
    try:
        row = db.one("SELECT p.*, c.params_json FROM pass_result p JOIN opt_job j USING (job_id) "
                     "JOIN candidate c ON c.candidate_id = p.candidate_id "
                     "WHERE j.round_id = ? AND p.candidate_id = ? ORDER BY p.job_id DESC LIMIT 1",
                     (round_id, candidate_id))
    finally:
        db.close()
    if row is None:
        typer.echo(f"no pass of {round_id} for {candidate_id}", err=True)
        raise typer.Exit(1)
    m = json.loads(row["metrics_json"])
    stats = json.loads(row["mt5_stats_json"])
    typer.echo(f"job {row['job_id']} pass {row['pass_no']} run {row['run_id']} params {row['params_json']}")
    typer.echo(f"net_profit={m['net_profit']} trades={m['trades']} deals_mt5={int(stats['deals'])} "
               f"xml_profit={row['xml_profit']} xml_trades={row['xml_trades']} days={row['daily_rows']}")
    for k in sorted(m):
        typer.echo(f"  {k}: {m[k]}")


@trials_app.command("show")
def trials_show(strategy_family: str, workspace: Path = WORKSPACE) -> None:
    """Every requested trial of a strategy family, including failed and discarded rounds (C44)."""
    db = _open(workspace)
    try:
        rows = db.all("SELECT t.round_id, t.chunk_index, t.study_id, t.n_trials_raw, t.created_at, r.from_date, "
                      "r.to_date FROM trial_ledger t JOIN optimization_round r USING (round_id) "
                      "WHERE t.strategy_family = ? ORDER BY t.created_at", (strategy_family,))
    finally:
        db.close()
    for r in rows:
        typer.echo(f"{r['created_at']} {r['round_id']} chunk={r['chunk_index']} study={r['study_id']} "
                   f"[{r['from_date']}, {r['to_date']}) trials={r['n_trials_raw']}")
    typer.echo(f"total trials (raw N): {sum(r['n_trials_raw'] for r in rows)}")


def _print_filter_summary(summary: dict) -> None:
    for k in ("filter_id", "round_id", "profile", "profile_hash", "component", "candidates", "passed", "failed",
              "filter_runs_on_this_round"):
        typer.echo(f"{k}: {summary[k]}")
    typer.echo("reasons (a candidate can have several):")
    for code, n in summary["reason_counts"].items():
        typer.echo(f"  {code}: {n}")
    for code, n in summary["warning_counts"].items():
        typer.echo(f"  warning {code}: {n}")
    c = summary["conditions"]
    typer.echo(f"conditions: deposit={c.get('deposit')} {c.get('currency')} leverage={c.get('leverage')} "
               f"fixed={c.get('fixed_inputs')} server={c.get('server')} (margin level is judged at these conditions)")
    typer.echo("note: passing means 'may go on to robustness/statistics', not a forecast of profit; "
               "PSR does not correct for the number of trials (DSR does, P6)")


@filter_app.command("run")
def filter_run(round_id: str,
               profile: str = typer.Option(..., "--profile", help="Strategy profile name, e.g. intraday"),
               profiles: Path = typer.Option(Path("configs/strategy_profiles.yaml"), "--profiles"),
               workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.filtering.run import run_filter

    out = run_filter(workspace, round_id, profiles, profile)
    if as_json:
        _emit({"status": out.status, "message": out.message, **out.summary}, True)
        raise typer.Exit(out.exit_code)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_filter_summary(out.summary)
    raise typer.Exit(out.exit_code)


@filter_app.command("show")
def filter_show(filter_id: str, workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.filtering.run import FilterError, summarize

    db = _open(workspace)
    try:
        try:
            summary = summarize(db, filter_id)
        except FilterError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(summary, True)
    else:
        _print_filter_summary(summary)


@app.command("why-not")
def why_not_cmd(candidate_id: str, filter_id: str | None = typer.Option(None, "--filter", help="Default: latest"),
                workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    """Every gate of the filter decision for one candidate: value, threshold, status (P4)."""
    from robustlab.filtering.run import FilterError, why_not

    db = _open(workspace)
    try:
        try:
            w = why_not(db, candidate_id, filter_id)
            from robustlab.selection.run import selection_of

            w["selection"] = selection_of(db, candidate_id)
        except FilterError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(w, True)
        return
    typer.echo(f"candidate: {w['candidate_id']}  params: {w['params']}")
    typer.echo(f"filter: {w['filter_id']} ({w['component']}, profile {w['profile']} {w['profile_hash']})  "
               f"outcome: {w['outcome']}")
    typer.echo("reasons: " + (", ".join(w["reasons"]) or "-") + ("  warnings: " + ", ".join(w["warnings"])
                                                                 if w["warnings"] else ""))
    typer.echo("gates:")
    for g in w["gates"]:
        note = f"  ({g['note']})" if g.get("note") else ""
        typer.echo(f"  {g['status']:<14} {g['gate']:<16} value={g['value']} threshold={g['threshold']}{note}")
    v = w["values"]
    typer.echo(f"psr: {v.get('psr')} (SR*={v.get('psr_sr_star')}, n={v.get('n')}, sharpe={v.get('sharpe')}, "
               f"skew={v.get('skewness')}, kurtosis={v.get('kurtosis')}, inputs {v.get('psr_input_definition')})")
    if v.get("min_trl") is not None:
        typer.echo(f"min_trl: {v['min_trl']:.1f} days (extra days needed: {v.get('extra_days_needed')})")
    else:
        typer.echo(f"min_trl: n/a ({v.get('min_trl_reason')})")
    typer.echo(f"recorded, not filtered (C51): equity_max_dd={v.get('equity_max_dd')} "
               f"({v.get('equity_max_dd_pct')}%), balance_max_dd={v.get('balance_max_dd')}")
    c = w["conditions"]
    typer.echo(f"conditions: deposit={c.get('deposit')} {c.get('currency')} leverage={c.get('leverage')} "
               f"fixed={c.get('fixed_inputs')} server={c.get('server')}")
    if w["decisions_for_candidate"] > 1:
        typer.echo(f"note: {w['decisions_for_candidate']} filter decisions exist for this candidate; "
                   "use --filter to pick one")
    if w.get("selection"):
        _print_selection_decision(w["selection"])


def _print_robust_summary(s: dict) -> None:
    for k in ("robust_id", "round_id", "filter_id", "component", "score", "candidates"):
        typer.echo(f"{k}: {s[k]}")
    typer.echo(f"score_status: {s['score_status_counts']}  (candidates whose own score is -inf, by cause)")
    typer.echo(f"robust_score: {s['robust_score_quantiles']}")
    typer.echo(f"peak_gap: {s['peak_gap_quantiles']}")
    typer.echo(f"axis_sensitivity (median |score change| per step): {s['axis_sensitivity']}")
    typer.echo(f"edge candidates (fewer than half the neighbours): {s['edge_candidates']}")
    typer.echo(f"regions: {len(s['regions'])}" + (f"  reason: {s['regions_reason']}" if s["regions_reason"] else ""))
    for r in s["regions"]:
        typer.echo(f"  {r['region_id']} size={r['member_count']} medoid={r['medoid_candidate_id']}")
    typer.echo(f"region_status: {s['region_status_counts']}")
    typer.echo(f"positive_year_share: {s['positive_year_share_distribution']}  (share of calendar years in profit)")
    typer.echo("note: values only, no pass/fail (P7 decides); every candidate is evaluated whether it passed P4 or not")


@robust_app.command("run")
def robust_run(round_id: str, filter_id: str = typer.Option(..., "--filter", help="P4 filter execution"),
               workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.robustness.run import run_robust

    out = run_robust(workspace, round_id, filter_id)
    if as_json:
        _emit({"status": out.status, "message": out.message, **out.summary}, True)
        raise typer.Exit(out.exit_code)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_robust_summary(out.summary)
    raise typer.Exit(out.exit_code)


@robust_app.command("show")
def robust_show(robust_id: str, candidate: str | None = typer.Option(None, "--candidate"),
                workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.robustness.run import RobustError, summarize

    db = _open(workspace)
    try:
        if candidate:
            row = db.one("SELECT values_json FROM evaluation WHERE robust_id = ? AND candidate_id = ?",
                         (robust_id, candidate))
            if row is None:
                typer.echo(f"no robustness values for {candidate} in {robust_id}", err=True)
                raise typer.Exit(1)
            v = json.loads(row["values_json"])
            if as_json:
                _emit(v, True)
                return
            for k in ("score", "score_status", "score_flags", "robust_score", "robust_score_reason", "peak_gap",
                      "peak_gap_reason", "peak_ratio", "peak_ratio_reason", "neighbours", "edge",
                      "neighbour_status_counts", "neighbour_pass_rate", "sensitivity_local", "region_status",
                      "region_id", "region_size", "subperiod"):
                typer.echo(f"{k}: {v.get(k)}")
            return
        try:
            s = summarize(db, robust_id)
        except RobustError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_robust_summary(s)


@robust_app.command("map")
def robust_map(robust_id: str, metric: str = typer.Option("score", "--metric", help="score or robust"),
               workspace: Path = WORKSPACE) -> None:
    from robustlab.robustness.run import RobustError, grid_map

    if metric not in ("score", "robust"):
        typer.echo("--metric must be score or robust", err=True)
        raise typer.Exit(1)
    db = _open(workspace)
    try:
        try:
            lines = grid_map(db, robust_id, metric)
        except RobustError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    for line in lines:
        typer.echo(line)


def _print_stats_summary(s: dict) -> None:
    for k in ("stat_id", "round_id", "filter_id", "strategy_family", "component", "sample_role", "period",
              "candidates"):
        typer.echo(f"{k}: {s[k]}")
    fam = s["family_inputs"]
    typer.echo(f"N (family, distinct requested configurations): {s['n_trials']}  "
               f"[round: {s['n_trials_round']}, trial ledger sum: {fam['trial_ledger_sum']}]")
    typer.echo(f"V[SR] (family): {s['var_sr']}  from {s['var_sr_count']} distinct (config, period) results  "
               f"[round: {s['var_sr_round']} from {s['var_sr_round_count']}]")
    typer.echo(f"excluded from V (still counted in N): {s['excluded']}")
    typer.echo(f"SR0: {s['sr0']}  [round scope: {s['sr0_round']}]" +
               (f"  reason: {fam['sr0_reason']}" if fam.get("sr0_reason") else ""))
    if fam.get("mixed_period_lengths"):
        typer.echo("note: the family mixes periods; Sharpe precision differs between them (V is larger, conservative)")
    ne = s["n_eff_reference"]
    typer.echo(f"N_eff (participation ratio, reference only): {ne.get('value')}"
               + (f"  reason: {ne['reason']}" if ne.get("reason") else "")
               + ("  (more trials than days: unstable)" if ne.get("rank_deficient") else ""))
    typer.echo(f"DSR standard: {s['dsr_standard']}")
    typer.echo(f"DSR conservative (min of standard / AR(1) / Newey-West / bootstrap SE): {s['dsr_conservative']}")
    typer.echo(f"SERIAL_DEPENDENCE_HIGH candidates: {s['serial_dependence_high']}")
    typer.echo("top by conservative DSR:")
    for t in s["top_by_conservative_dsr"]:
        typer.echo(f"  {t['candidate_id']} conservative={t['dsr_conservative']} standard={t['dsr']} "
                   f"ar1={t['dsr_ar1']} nw={t['dsr_newey_west']} rho1={t['rho1']} n*={t['n_ar1']}")
    typer.echo(f"bootstrap: B={s['bootstrap_reps']} seed={s['seed']} arch {s['arch_version']} "
               f"(stationary main, circular sensitivity, percentile 90%)")
    if not s["bootstrap"]:
        typer.echo("  no bootstrap targets (no P4 pass, no P5 medoid, none requested with --candidate)")
    for b in s["bootstrap"]:
        typer.echo(f"  {b}")
    typer.echo("known limitations:")
    for x in s["known_limitations"]:
        typer.echo(f"  - {x}")
    typer.echo(f"note: {s['note']}")


@stats_app.command("run")
def stats_run(round_id: str, filter_id: str = typer.Option(..., "--filter", help="P4 filter execution"),
              candidate: list[str] = typer.Option(None, "--candidate", help="Also bootstrap this candidate"),
              reps: int = typer.Option(2000, "--reps"), seed: int | None = typer.Option(None, "--seed"),
              workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.stat_eval.run import run_stats

    out = run_stats(workspace, round_id, filter_id, candidate or [], reps, seed)
    if as_json:
        _emit({"status": out.status, "message": out.message, **out.summary}, True)
        raise typer.Exit(out.exit_code)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_stats_summary(out.summary)
    raise typer.Exit(out.exit_code)


@stats_app.command("show")
def stats_show(stat_id: str, candidate: str | None = typer.Option(None, "--candidate"),
               workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.stat_eval.run import NOTE, StatError, summarize

    db = _open(workspace)
    try:
        if candidate:
            row = db.one("SELECT values_json FROM evaluation WHERE stat_id = ? AND candidate_id = ?",
                         (stat_id, candidate))
            if row is None:
                typer.echo(f"no statistics for {candidate} in {stat_id}", err=True)
                raise typer.Exit(1)
            v = json.loads(row["values_json"])
            if as_json:
                _emit(v, True)
                return
            for k in ("sample_role", "period", "score_status", "sharpe", "days", "skewness", "kurtosis", "in_var_sr",
                      "warnings", "bootstrap_target_reasons"):
                typer.echo(f"{k}: {v.get(k)}")
            d = v["dsr"]
            for k in ("sr0", "dsr", "dsr_ar1", "dsr_newey_west", "dsr_bootstrap_se", "dsr_conservative",
                      "dsr_conservative_components", "usage"):
                typer.echo(f"{k}: {d.get(k)}")
            for k in ("reason", "dsr_reason", "dsr_bootstrap_se_reason"):
                if d.get(k):
                    typer.echo(f"{k}: {d[k]}")
            if d.get("dependence"):
                typer.echo(f"dependence: {d['dependence']}")
            typer.echo(f"dsr_round_scope (sensitivity): {v.get('dsr_round_scope')}")
            b = v.get("bootstrap")
            if b is None:
                typer.echo("bootstrap: not a target (not a P4 pass or P5 medoid; use --candidate to request)")
            elif b["status"] != "OK":
                typer.echo(f"bootstrap: {b['status']} ({b['reason']})")
            else:
                st, cb = b["stationary"], b["circular_sensitivity"]
                typer.echo(f"bootstrap: B={b['reps']} seed={b['seed']} block length {b['block_length']}")
                typer.echo(f"  point: {b['point']}")
                typer.echo(f"  mean 90% CI: {st['mean_ci']}  (amount: {st['mean_amount_ci']})")
                typer.echo(f"  sharpe 90% CI: {st['sharpe_ci']}" +
                           (f"  reason: {st['sharpe_ci_reason']}" if st["sharpe_ci_reason"] else ""))
                typer.echo(f"  resampled max DD (daily close, account currency) 5/50/95%: {st['max_dd_distribution']}")
                typer.echo(f"  resampled max DD %: {st['max_dd_pct_distribution']}  "
                           f"resampled bankrupt share: {st['resampled_bankrupt_share']}")
                typer.echo(f"  circular sensitivity: mean {cb['mean_ci']} sharpe {cb['sharpe_ci']} "
                           f"max DD {cb['max_dd_distribution']}")
                if b.get("sensitivity_note"):
                    typer.echo(f"  sensitivity note: {b['sensitivity_note']}")
                typer.echo("  note: daily-close DD is not the tick-level equity DD of P2 (equity_max_dd)")
            typer.echo(f"note: {NOTE}")
            return
        try:
            s = summarize(db, stat_id)
        except StatError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_stats_summary(s)


def _print_selection_decision(sd: dict) -> None:
    det = sd["details"]
    typer.echo(f"selection: {sd['sel_id']} ({sd['component']}, profile {det['profile']} {det['profile_hash'][:16]})  "
               f"outcome: {sd['outcome']}")
    typer.echo("  reasons: " + (", ".join(sd["reasons"]) or "-") + "  warnings: " + (", ".join(sd["warnings"]) or "-"))
    for g in det["gates"]:
        note = f"  ({g['note']})" if g.get("note") else ""
        typer.echo(f"  {g['status']:<14} {g['gate']:<16} value={g['value']} threshold={g['threshold']}{note}")
    nb = det["neighbourhood"]
    typer.echo(f"  neighbourhood (R3, 独自拡張): counted={nb['r3']['counted']} floor={nb['floor']} "
               f"no_trade_left_out={nb['r3']['no_trade']} ruin={nb['r3']['ruin']} "
               f"robust_score={nb['r3']['robust_score']}  | P5 standard: robust_score={nb['p5']['robust_score']}")
    rk = det["rank"]
    if rk["layer"] is not None:
        typer.echo(f"  rank: layer={rk['layer']} mean_rank={rk['mean_rank']} ranks={rk['ranks']} "
                   f"selected_rank={rk['selected_rank']} inputs={rk['inputs']}")
    typer.echo("  note: DSR is used for relative ranking only, never as a gate (C62)")


def _print_select_summary(s: dict) -> None:
    for k in ("sel_id", "round_id", "filter_id", "robust_id", "stat_id", "selector", "profile", "budget"):
        typer.echo(f"{k}: {s[k]}")
    typer.echo(f"candidates: {s['candidates']}  P4 pass (eligible): {s['eligible_p4_pass']}  "
               f"gate pass: {s['gate_pass']}  region representatives: {s['representatives']}  "
               f"selected: {s['selected_count']}")
    if s["outcome_reason"]:
        typer.echo(f"outcome_reason: {s['outcome_reason']}")
    if s["revision_no"]:
        typer.echo(f"SELECTION_REVISED: this round was selected {s['revision_no']} time(s) before with other "
                   "inputs or settings")
    typer.echo(f"outcomes: {s['outcomes']}")
    typer.echo(f"reasons: {s['reason_counts']}")
    typer.echo(f"warnings: {s['warning_counts']}")
    typer.echo("selected (integrated rank order):")
    if not s["selected"]:
        typer.echo("  none")
    for x in s["selected"]:
        typer.echo(f"  {x['rank']}. {x['candidate_id']} {x['params']} layer={x['layer']} mean_rank={x['mean_rank']} "
                   f"robust={x['robust_score_r3']} dsr={x['dsr_conservative']} maxDD95={x['max_dd_p95']} "
                   f"warnings={x['warnings']}")
    typer.echo("known limitations:")
    for t in s["known_limitations"]:
        typer.echo(f"  - {t}")
    typer.echo(f"note: {s['note']}")


@select_app.command("run")
def select_run(round_id: str, filter_id: str = typer.Option(..., "--filter", help="P4 filter execution"),
               robust_id: str = typer.Option(..., "--robust", help="P5 robustness execution"),
               stat_id: str = typer.Option(..., "--stats", help="P6 statistics execution"),
               profile: str = typer.Option("default", "--profile"),
               profiles: Path = typer.Option(Path("configs/selection_profiles.yaml"), "--profiles"),
               workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.selection.run import run_select

    out = run_select(workspace, round_id, filter_id, robust_id, stat_id, profiles, profile)
    if as_json:
        _emit({"status": out.status, "message": out.message, **out.summary}, True)
        raise typer.Exit(out.exit_code)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_select_summary(out.summary)
    raise typer.Exit(out.exit_code)


@select_app.command("show")
def select_show(sel_id: str, candidate: str | None = typer.Option(None, "--candidate"),
                workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.selection.run import SelectError, selection_of, summarize

    db = _open(workspace)
    try:
        if candidate:
            sd = selection_of(db, candidate, sel_id)
            if sd is None:
                typer.echo(f"no selection decision for {candidate} in {sel_id}", err=True)
                raise typer.Exit(1)
            if as_json:
                _emit(sd, True)
            else:
                _print_selection_decision(sd)
            return
        try:
            s = summarize(db, sel_id)
        except SelectError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_select_summary(s)


def _print_validate_summary(s: dict) -> None:
    for k in ("plan_id", "sel_id", "round_id", "label", "diagnostic", "revision_no", "locked_at"):
        typer.echo(f"{k}: {s[k]}")
    if s["diagnostic"]:
        typer.echo("DIAGNOSTIC: checks the machinery only; no decision is written and nothing here is a pass/fail")
    typer.echo("periods: " + ", ".join(f"{p['name']} [{p['from']}, {p['to']})" for p in s["periods"]))
    typer.echo(f"candidates: {s['candidates']}")
    typer.echo(f"gate settings: {s['constants']}  extra cost (price width per round trip): {s['cost']['symbols']}")
    typer.echo(f"plans that have seen each period: {s['plans_that_saw_each_period']}  "
               f"exposure before lock: {len(s['exposure_before_lock'])}")
    typer.echo("runs:")
    for r in s["runs"]:
        typer.echo(f"  {r['candidate_id']} {r['period_name']:<9} attempt {r['attempt_no']} {r['status']}"
                   f"{' (reused existing result)' if r['reused'] else ''} run={r['run_id']}")
    typer.echo("results:")
    for x in s["results"]:
        if "outcome" not in x:
            typer.echo(f"  {x['candidate_id']}: not evaluated yet")
            continue
        typer.echo(f"  {x['candidate_id']}: {x['outcome']}  reasons={x['reasons'] or '-'}")
        typer.echo(f"    p={x['p_value']:.6g} (PSR: {x['psr']}, n={x['n_days']} days)  pooled net={x['pooled_net_profit']}"
                   f"  after extra cost={x['net_after_cost_1x']}")
        typer.echo(f"    reproducibility: {x['reproducibility']}")
        for n, p in x["periods"].items():
            typer.echo(f"    {n}: net={p['net_profit']} trades={p['trades']} days={p['days']} sharpe={p['sharpe']} "
                       f"observed spread={p['observed_spread_points']} points")
        if "VALIDATION_INSUFFICIENT_EVIDENCE" in x["reasons"]:
            typer.echo(f"    note: {s['insufficient_evidence_text']}")
    typer.echo(f"known limitation: {s['known_limitation']}")


@validate_app.command("lock")
def validate_lock(sel_id: str, plan: Path = typer.Option(..., "--plan", exists=True, dir_okay=False),
                  cost: Path = typer.Option(Path("configs/cost_stress.yaml"), "--cost"),
                  partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition"),
                  diagnostic: bool = typer.Option(False, "--diagnostic", help="Check the machinery, no decision"),
                  candidate: str | None = typer.Option(None, "--candidate", help="Candidate for --diagnostic"),
                  workspace: Path = WORKSPACE) -> None:
    from robustlab.validation.run import lock

    if diagnostic != bool(candidate):
        typer.echo("--diagnostic and --candidate go together", err=True)
        raise typer.Exit(1)
    out = lock(workspace, sel_id, plan, cost, partition, candidate)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_validate_summary(out.summary)
    raise typer.Exit(out.exit_code)


@validate_app.command("run")
def validate_run(plan_id: str, terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal"),
                 partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition"),
                 workspace: Path = WORKSPACE) -> None:
    from robustlab.validation.run import log_access, run

    out = run(workspace, plan_id, terminal, partition)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        db = _open(workspace)
        try:
            log_access(db, plan_id, None, "run")
        finally:
            db.close()
        _print_validate_summary(out.summary)
    raise typer.Exit(out.exit_code)


@validate_app.command("show")
def validate_show(plan_id: str, candidate: str | None = typer.Option(None, "--candidate"),
                  workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.validation.run import ValidateError, log_access, summarize

    db = _open(workspace)
    try:
        try:
            s = summarize(db, plan_id)
        except ValidateError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
        log_access(db, plan_id, candidate, "show")  # P8_PLAN §5: every look at the results is recorded
        if candidate:
            row = db.one("SELECT values_json FROM evaluation WHERE plan_id = ? AND candidate_id = ?",
                         (plan_id, candidate))
            if row is None:
                typer.echo(f"no validation values for {candidate} in {plan_id}", err=True)
                raise typer.Exit(1)
            _emit(json.loads(row["values_json"]), True)
            return
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_validate_summary(s)


def _print_oos_summary(s: dict) -> None:
    for k in ("manifest_id", "plan_id", "round_id", "rehearsal", "holdout", "holdout_state", "burned_at", "locked_at"):
        typer.echo(f"{k}: {s[k]}")
    if s["rehearsal"]:
        typer.echo("REHEARSAL: practice workspace; no decision is written")
    typer.echo(f"candidates: {s['candidates']}")
    typer.echo(f"criteria (fixed before the run): {s['criteria']}")
    for r in s["runs"]:
        typer.echo(f"  run {r['candidate_id']} attempt {r['attempt_no']} {r['status']} run={r['run_id']}")
    typer.echo("results:")
    for x in s["results"]:
        typer.echo(f"  {x['candidate_id']}: band lower edge {x['band_lower']:.6g} (centre {x['band_center']:.6g})")
        if "outcome" in x:
            typer.echo(f"    {x['outcome']}  reasons={x['reasons'] or '-'}  OOS sharpe={x['oos_sharpe']}  "
                       f"net={x['net_profit']}  after extra cost={x['net_after_cost']}  days={x['days']}  "
                       f"trades={x['trades']}  p (recorded only)={x['p_value_recorded_only']}")
            typer.echo(f"    meaning: {x['interpretation']}")
    typer.echo(f"note: {s['simulation_note']}")
    typer.echo(f"note: {s['reuse_warning']}")


@oos_app.command("lock")
def oos_lock(plan_id: str, partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition"),
             cost: Path = typer.Option(Path("configs/cost_stress.yaml"), "--cost"),
             rehearsal: bool = typer.Option(False, "--rehearsal", help="Practice workspace only"),
             candidate: str | None = typer.Option(None, "--candidate", help="Candidate for --rehearsal"),
             workspace: Path = WORKSPACE) -> None:
    from robustlab.oos.manifest import lock

    if rehearsal != bool(candidate):
        typer.echo("--rehearsal and --candidate go together", err=True)
        raise typer.Exit(1)
    out = lock(workspace, plan_id, partition, cost, candidate)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_oos_summary(out.summary)
    raise typer.Exit(out.exit_code)


@oos_app.command("run")
def oos_run(manifest_id: str, confirm: bool = typer.Option(False, "--i-understand-this-is-final",
                                                         help="Run the OOS once and burn the holdout"),
            terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal"),
            partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition"),
            workspace: Path = WORKSPACE) -> None:
    from robustlab.oos.manifest import log_access, run

    out = run(workspace, manifest_id, terminal, partition, confirm)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        db = _open(workspace)
        try:
            log_access(db, manifest_id, "run")
        finally:
            db.close()
        _print_oos_summary(out.summary)
    raise typer.Exit(out.exit_code)


@oos_app.command("show")
def oos_show(manifest_id: str, workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.oos.manifest import OosError, log_access, summarize

    db = _open(workspace)
    try:
        try:
            s = summarize(db, manifest_id)
        except OosError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
        log_access(db, manifest_id, "show")
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_oos_summary(s)


def _fmt(v: object) -> str:
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def _print_risk_summary(s: dict, candidate: str | None = None) -> None:
    for k in ("risk_id", "plan_id", "symbol", "equity", "account_currency", "equity_overridden", "snapshot_taken_at",
              "volume_min", "volume_step", "margin_per_lot", "stop_out", "version"):
        typer.echo(f"{k}: {s[k]}")
    p = s["profile"]
    typer.echo(f"limits: P(DD > {p['max_dd']:.0%}) <= {p['dd_alpha']:.0%}; P(lose {p['ruin_loss']:.0%}) <= "
               f"{p['ruin_p']:.0%}; P(stop-out) <= {p['stop_out_p']:.0%}; {p['horizon_days']} days, "
               f"{p['n_paths']} paths per scenario, 95% upper bounds")
    for r in s["results"]:
        if candidate and r["candidate_id"] != candidate:
            continue
        typer.echo(f"candidate {r['candidate_id']}: {r['outcome']}  (eligibility {r['eligibility']})")
        if r["eligibility"] == "DIAGNOSTIC":
            typer.echo("  DIAGNOSTIC: not a candidate for allocation (no P8a pass); risk per lot only, no lot")
        if r.get("error"):
            typer.echo(f"  error: {r['error']}")
        if "lot_target" in r:
            typer.echo(f"  lot: {r['lot']}  (target {_fmt(r['lot_target'])} = raw {_fmt(r['lot_raw'])} x confidence "
                       f"{r['confidence_weight']} x ramp-up {r['ramp_up']}; rounded DOWN)")
            typer.echo(f"  limits (lots): " + ", ".join(f"{k} {_fmt(v)}" for k, v in r["limits"].items())
                       + f"; binding {r['binding_constraint']}; safe lot set by {r['safe_binding']}")
            if r["outcome"] in ("INFEASIBLE_MIN_LOT", "MARGIN_BUDGET_EXCEEDED"):
                need = r["sizing_notes"].get("equity_needed_for_min_lot")
                typer.echo(f"  not rounded up to the minimum lot {s['volume_min']}; equity that would make the minimum "
                           f"lot fit these limits: {_fmt(need)} {s['account_currency']} (information only, nothing is "
                           "allocated automatically)")
        if r.get("at_final_lot"):
            typer.echo("  at the final lot (upper bound 95% / limit):")
            for n, kv in r["at_final_lot"].items():
                typer.echo(f"    {n}: " + ", ".join(f"{k} {_fmt(v['upper_bound_95'])}/{v['limit']}" for k, v in kv.items())
                           + f"; DD vs equity q95 {r['dd_vs_equity_at_final_lot'][n]['q95']:.2%} "
                             f"q99 {r['dd_vs_equity_at_final_lot'][n]['q99']:.2%}")
        if r.get("scenarios"):
            typer.echo(f"  risk per 1 lot ({s['account_currency']}):")
            for n, it in r["scenarios"].items():
                pl = it["per_lot"]
                typer.echo(f"    {n} [{it['role']}]: max DD q50 {_fmt(pl['max_dd_per_lot_q50'])} q95 "
                           f"{_fmt(pl['max_dd_per_lot_q95'])} q99 {_fmt(pl['max_dd_per_lot_q99'])}; underwater days q95 "
                           f"{_fmt(pl['underwater_days_q95'])}; below peak at end {pl['share_below_peak_at_end']:.1%}; "
                           f"losing-day run q95 {_fmt(pl['losing_days_run_q95'])}")
        if r.get("observed"):
            o = r["observed"]
            typer.echo(f"  observed: longest losing days {o['longest_losing_days']}, losing trades "
                       f"{o['longest_losing_trades']}, max floating loss per lot {_fmt(o['max_floating_loss_per_lot'])}, "
                       f"min margin level {o['min_margin_level']}")
    for n in s["notes"]:
        typer.echo(f"note: {n}")


@risk_app.command("snapshot")
def risk_snapshot(symbol: str = typer.Option(..., "--symbol"),
                  cost: Path = typer.Option(Path("configs/cost_stress.yaml"), "--cost"),
                  out: Path | None = typer.Option(None, "--out", help="Default: <workspace>/risk_snapshots/"),
                  workspace: Path = WORKSPACE) -> None:
    """Windows only: broker values from the running terminal (no login, name or server is stored)."""
    import datetime as dt

    from robustlab.config.loader import ConfigError, load_cost_stress
    from robustlab.risk import snapshot as snp

    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError:
        typer.echo("the MetaTrader5 package is not available (Windows only)", err=True)
        raise typer.Exit(1)
    try:
        cs, _ = load_cost_stress(cost)
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    if symbol not in cs.symbols:
        typer.echo(f"{symbol} has no cost-stress width in {cost}", err=True)
        raise typer.Exit(1)
    try:
        snap = snp.take(mt5, symbol, cs.symbols[symbol].extra_price_per_round_trip)
    except snp.SnapshotError as e:
        typer.echo(f"snapshot failed: {e}", err=True)
        raise typer.Exit(2) from e
    path = out or workspace / "risk_snapshots" / f"{symbol}_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    digest = snp.save(snap, path)
    typer.echo(f"snapshot: {path}")
    typer.echo(f"sha256: {digest}")
    for k in ("account", "symbol_info", "price", "margin_per_lot", "stress_cost_per_lot"):
        typer.echo(f"{k}: {snap[k]}")


@risk_app.command("run")
def risk_run(plan_id: str, snapshot: Path = typer.Option(..., "--snapshot", exists=True, dir_okay=False),
             profile: str = typer.Option("default", "--profile"),
             profiles: Path = typer.Option(Path("configs/risk_profiles.yaml"), "--profiles"),
             equity: float | None = typer.Option(None, "--equity", help="What-if equity (recorded as overridden)"),
             candidate: str | None = typer.Option(None, "--candidate"),
             workspace: Path = WORKSPACE) -> None:
    from robustlab.risk.run import run

    out = run(workspace, plan_id, snapshot, profiles, profile, equity, candidate)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_risk_summary(out.summary)
    raise typer.Exit(out.exit_code)


@risk_app.command("show")
def risk_show(risk_id: str, candidate: str | None = typer.Option(None, "--candidate"),
              workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.risk.run import RiskError, summarize

    db = _open(workspace)
    try:
        try:
            s = summarize(db, risk_id)
        except RiskError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    if as_json:
        _emit(s, True)
    else:
        _print_risk_summary(s, candidate)


def _print_null_summary(s: dict) -> None:
    for k in ("null_id", "archetype", "mode", "kind", "rounds", "seed", "evidence_seeding", "version", "created_at"):
        typer.echo(f"{k}: {s[k]}")
    r = s["result"]
    c = s["criterion"]
    typer.echo(f"verdict: {s['verdict']}"
               + (f"  (criterion: one-sided 95% upper bound <= {c['max_upper_bound']:.0%})" if s["mode"] == "judgement"
                  else "  (not a judgement)"))
    typer.echo(f"chance pass: {r['pass_rounds']} of {r['rounds']} rounds = {r['pass_rate']:.4f}; 95% CI "
               f"{r['ci']['lower']:.4f}-{r['ci']['upper']:.4f}; one-sided upper {r['ci']['upper_one_sided']:.4f}")
    typer.echo(f"per round: P4 passes {r['p4_mean']:.2f}, selected {r['selected_mean']:.2f}")
    typer.echo(f"S1: mean gross P&L {r['s1']['gross_mean']:.2f} (SE {r['s1']['se']:.2f}, z {r['s1']['z']:.2f}; "
               f"must be within {c['s1_max_se']} SE)")
    if r["passers"]:
        ci = r["passers_ruined_ci"]
        typer.echo(f"passers: {r['passers']}; ruined in the next year {r['passers_ruined_next_year']} (95% CI "
                   f"{ci['lower']:.3f}-{ci['upper']:.3f}); next-year net mean {r['passers_next_year_net_mean']:.1f}")
    if r.get("s3"):
        x = r["s3"]
        typer.echo(f"S3: lanes never stopped out in D+V0+V1 {x['lanes']} of {x['of']}; P(stop-out next year) EA re-run "
                   f"{x['structural']:.3f} vs shuffle of its own closed P&L {x['naive_shuffle']:.4f}")
    for n in s["notes"]:
        typer.echo(f"note: {n}")


@null_app.command("run")
def null_run(archetype: str = typer.Option(..., "--archetype"),
             mode: str = typer.Option(..., "--mode", help="judgement (once), reproduction or power"),
             config: Path = typer.Option(Path("configs/null_archetypes.yaml"), "--config"),
             profiles: Path = typer.Option(Path("configs/strategy_profiles.yaml"), "--profiles"),
             workspace: Path = WORKSPACE) -> None:
    from robustlab.nulltest.run import run

    def progress(done: int, total: int) -> None:
        typer.echo(f"  {done}/{total} rounds", err=True)

    out = run(workspace, archetype, mode, config, profiles, progress)
    typer.echo(f"status: {out.status}")
    if out.message:
        typer.echo(f"message: {out.message}")
    if out.summary:
        _print_null_summary(out.summary)
    raise typer.Exit(out.exit_code)


@null_app.command("show")
def null_show(null_id: str, workspace: Path = WORKSPACE, as_json: bool = typer.Option(False, "--json")) -> None:
    from robustlab.nulltest.run import NullError, summarize

    db = _open(workspace)
    try:
        try:
            s = summarize(db, null_id)
        except NullError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from e
    finally:
        db.close()
    _emit(s, True) if as_json else _print_null_summary(s)


@null_app.command("fidelity-export")
def null_fidelity_export(days: int = typer.Option(260, "--days"),
                         terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal"),
                         config: Path = typer.Option(Path("configs/null_archetypes.yaml"), "--config"),
                         out: Path | None = typer.Option(None, "--out", help="Default: <Common\\Files>/RL_H0_EURUSD_M1.csv")) -> None:
    """Write the synthetic H0 M1 bars for the custom symbol (P10 fidelity check)."""
    from robustlab.config.loader import ConfigError, load_terminal_config
    from robustlab.nulltest import fidelity as fd
    from robustlab.nulltest import sim
    from robustlab.nulltest.run import load_config

    cfg, _ = load_config(config)
    if out is None:
        try:
            out = load_terminal_config(terminal).common_files_dir / "RL_H0_EURUSD_M1.csv"
        except (ConfigError, OSError) as e:
            typer.echo(f"no --out and no terminal config: {e}", err=True)
            raise typer.Exit(1) from e
    info = fd.export(out, days, sim.PriceModel(**cfg["price"]), cfg["account"]["start_price"])
    typer.echo(f"written: {out}")
    for k, v in info.items():
        typer.echo(f"{k}: {v}")
    typer.echo("next: run mql5/Scripts/RobustLab/RL_ImportH0.mq5 on a chart, then "
               "`rlab backtest run configs/requests/p10_fidelity_h4.yaml --workspace workspace_p10fid`")


@null_app.command("fidelity-compare")
def null_fidelity_compare(run_id: str | None = typer.Argument(None, help="Default: the latest successful run on "
                                                                         "EURUSD_RLH0 in the workspace"),
                          days: int = typer.Option(260, "--days"),
                          fast: int = typer.Option(12, "--fast"), slow: int = typer.Option(48, "--slow"),
                          config: Path = typer.Option(Path("configs/null_archetypes.yaml"), "--config"),
                          workspace: Path = WORKSPACE) -> None:
    """Compare the tester's deals with the exact RL_SmokeTest rules on the same synthetic prices."""
    from robustlab.mt5 import telemetry_reader as tr
    from robustlab.nulltest import fidelity as fd
    from robustlab.nulltest import sim
    from robustlab.nulltest.run import load_config
    from robustlab.storage.artifacts import ArtifactStore

    cfg, _ = load_config(config)
    pm = sim.PriceModel(**cfg["price"])
    db = _open(workspace)
    try:
        if run_id is None:
            job = db.one("SELECT j.job_id, j.run_id FROM job j JOIN run r USING (run_id) JOIN candidate c USING "
                         "(candidate_id) WHERE c.symbol = ? AND j.status = 'SUCCEEDED' ORDER BY j.created_at DESC "
                         "LIMIT 1", (fd.SYMBOL,))
        else:
            job = db.one("SELECT job_id, run_id FROM job WHERE run_id = ? AND status = 'SUCCEEDED' ORDER BY job_id "
                         "DESC LIMIT 1", (run_id,))
        if job is None:
            typer.echo(f"no successful job for {run_id or fd.SYMBOL}", err=True)
            raise typer.Exit(1)
        typer.echo(f"run: {job['run_id']}  job: {job['job_id']}")
        row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = 'telemetry_deals'", (job["job_id"],))
        text = ArtifactStore(workspace.resolve() / "artifacts", db).read(row["sha256"]).decode("utf-8-sig")
    finally:
        db.close()
    deals = tr.parse_csv_text(text, tr.DEALS_COLUMNS, "deals")
    model = fd.smoketest_model(fd.path_of(days, pm, cfg["account"]["start_price"]), days, fast, slow, pm)
    res = fd.compare(model, deals)
    for k in ("model_deals", "tester_deals", "mismatches"):
        typer.echo(f"{k}: {res[k]}")
    for m in res["first"]:
        typer.echo(f"  #{m['i']}: model {m['model']} | tester {m['tester']}")
    typer.echo("diagnosis (does not change the verdict above):")
    for k, v in res["diagnosis"].items():
        typer.echo(f"  {k}: {v}")
    typer.echo("=== v1 (pre-registered first, kept as recorded) ===")
    typer.echo(f"v1 match: {res['match']}")
    v2 = fd.compare_v2(model, deals)
    typer.echo("=== v2 (defined 2026-10-12 before the re-verification: same M1 bar, end-of-test apart, profits) ===")
    for k, v in v2.items():
        typer.echo(f"{k}: {v}")
    raise typer.Exit(0 if v2["match"] else 2)



studya_app = typer.Typer(no_args_is_help=True, help="Study A: the machine's safety and detection power (T1 = RL_TSMom)")
app.add_typer(studya_app, name="studya")
STUDYA_CFG = typer.Option(Path("configs/study_a.yaml"), "--config")


def _studya_progress(done: int, total: int) -> None:
    typer.echo(f"  {done}/{total} rounds", err=True)


def _studya_print(s: dict) -> None:
    typer.echo(f"name: {s['name']}  rounds: {s['rounds']}  detected (P8a PASS) rounds: {s['detected_rounds']}")
    ci = s["ci"]
    if s["measurement"].get("edge") is None:
        typer.echo(f"false-pass rate: {s['rate']:.4f}  one-sided 95% upper: {ci['upper_one_sided']:.4f}  "
                   f"criterion <= {s['criterion']['max_upper_bound']}  verdict: {s.get('verdict')}")
        typer.echo(f"S1: gross mean {s['s1']['gross_mean']:.2f}  z {s['s1']['z']}")
    else:
        typer.echo(f"detection rate: {s['rate']:.4f}  95% CI {ci['lower']:.4f}-{ci['upper']:.4f}  "
                   f"(edge {s['measurement']['edge']}, strength {s['measurement']['strength']}, scale {s['edge_scale']:.6g})")
    typer.echo(f"funnel (mean per round): {s['funnel_mean_per_round']}  rounds with: {s['rounds_with']}")
    typer.echo(f"timing: {s['timing']}")
    for n in s["notes"]:
        typer.echo(f"note: {n}")


@studya_app.command("calibrate")
def studya_calibrate(config: Path = STUDYA_CFG,
                     provisional_out: Path | None = typer.Option(
                         None, "--provisional-out", help="Write a provisional copy here (no fidelity gate; never "
                                                         "used by a measurement)")) -> None:
    """Fix the edge strengths (SPEC §4.3, §12.4: 40 worlds, per-world Sharpe) after the fidelity check matched;
    refuses to overwrite."""
    from robustlab.studya import run as R

    try:
        out, h = R.calibration(config, lambda s: typer.echo(f"  {s}", err=True), out_path=provisional_out)
    except (RuntimeError, FileExistsError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(f"calibration saved (sha256 {h[:16]}){' PROVISIONAL' if provisional_out else ''}")
    for name, res in out["edges"].items():
        for t, v in res.items():
            r = v["reference"]
            typer.echo(f"{name} s={t}: scale {v['scale']:.6g}  reference mean {r['mean']:.4f} (target diff "
                       f"{r['diff_from_target']:+.4f}, se {r['se_mean']:.4f}, median {r['median']:.4f}, sd {r['sd']:.4f}, "
                       f"min {r['min']:.3f}, max {r['max']:.3f})  best {v['max_cell_by_mean']} {v['max_mean']:.4f}")


@studya_app.command("price-check")
def studya_price_check(config: Path = STUDYA_CFG) -> None:
    """Price model verification (SPEC §12.5) -> results/price_model_check.json (re-run after the calibration)."""
    from robustlab.studya import pricecheck as PC
    from robustlab.studya import run as R

    out = PC.run(config, progress=lambda s: typer.echo(f"  {s}", err=True))
    h = PC.save(out, R.RESULTS / "price_model_check.json")
    sp = out["s_paths"]
    typer.echo(f"same draws as P10: {out['same_as_p10']}")
    typer.echo(f"S paths: all positive, price {sp['price_min']['min']:.4f}-{sp['price_max']['max']:.4f}; arithmetic "
               f"(P10-style) paths reaching 0: {sp['arithmetic_paths_reaching_0']}/{sp['rounds']}")
    typer.echo(f"absolute width ratio multiplicative/arithmetic: {sp['absolute_width']['ratio_multiplicative_to_arithmetic']}")
    typer.echo(f"strength 0 = H0: {out['strength_zero_is_h0']}  P paths: "
               f"{ {k: (v['price_min'], v['price_max'], len(v['non_positive_rounds'])) for k, v in (out['p_paths'] or {}).items()} }")
    for i in out["issues"]:
        typer.echo(f"issue: {i}")
    typer.echo(f"saved (sha256 {h[:16]}): {'OK' if out['ok'] else 'NOT OK'}")
    raise typer.Exit(0 if out["ok"] else 2)


@studya_app.command("status")
def studya_status(config: Path = STUDYA_CFG, workers: int = typer.Option(4, "--workers")) -> None:
    """Gates before the main measurement (fidelity -> calibration -> price check) and the time projection."""
    from robustlab.studya import evaluate as E
    from robustlab.studya import run as R

    st = E.setup(config)
    for g, ok, msg in R.gates(st):
        typer.echo(f"{g}: {'OK' if ok else 'NOT READY'}  {msg}")
    h = R.projected_hours(st, list(st.cfg["measurements"]), workers)
    typer.echo(f"projected wall time for all measurements with {workers} workers: "
               f"{'unknown (no pilot)' if h is None else f'{h:.1f} h'} (limit {R.MAX_HOURS:.0f} h)")


@studya_app.command("pilot")
def studya_pilot(config: Path = STUDYA_CFG, workers: int = typer.Option(4, "--workers"),
                 calibration: Path | None = typer.Option(None, "--calibration")) -> None:
    """Pilot (SPEC §6.4): separate seeds, not counted. Writes results/pilot_*.json."""
    from robustlab.studya import evaluate as E
    from robustlab.studya import run as R

    cfg, _ = E.load_config(config)
    for key, m in cfg["pilot"].items():
        s, rounds = R.execute(config, f"pilot_{key}", m, workers=workers, calib_path=calibration,
                              progress=_studya_progress)
        R.save(s, rounds, R.RESULTS, f"pilot_{key}", refuse_existing=False)
        _studya_print(s)


@studya_app.command("measure")
def studya_measure(name: str = typer.Argument(..., help="S, P_E1_0.5, ... or ALL"), config: Path = STUDYA_CFG,
                   workers: int = typer.Option(4, "--workers"),
                   rerun_reason: str | None = typer.Option(None, "--rerun-reason",
                                                           help="Only after a technical failure (recorded)")) -> None:
    """Main measurement (SPEC §6, §12.6): only after fidelity -> calibration -> price check; each measurement ONCE."""
    from robustlab.studya import evaluate as E
    from robustlab.studya import run as R

    st = E.setup(config)
    cfg = st.cfg
    names = list(cfg["measurements"]) if name == "ALL" else [name]
    if any(n not in cfg["measurements"] for n in names):
        typer.echo(f"unknown measurement in {names}", err=True)
        raise typer.Exit(1)
    blocked = [(g, msg) for g, ok, msg in R.gates(st) if not ok]
    if blocked:
        for g, msg in blocked:
            typer.echo(f"refused: {g}: {msg}", err=True)
        raise typer.Exit(1)
    todo = [n for n in names if not R.already(n)]
    h = R.projected_hours(st, todo, workers)
    if h is None or h > R.MAX_HOURS:
        typer.echo(f"refused: projected wall time {h} h (limit {R.MAX_HOURS} h, no approximation): report first",
                   err=True)
        raise typer.Exit(1)
    typer.echo(f"projected wall time {h:.1f} h for {todo}")
    for n in names:
        if R.already(n):
            typer.echo(f"{n}: ALREADY_MEASURED (each measurement runs once)", err=True)
            continue
        try:
            marker = R.begin(st, n, rerun_reason, workers)
        except (RuntimeError, FileExistsError) as e:
            typer.echo(f"refused: {e}", err=True)
            raise typer.Exit(1) from e
        calib = R.RESULTS / "calibration.json"
        s, rounds = R.execute(config, n, cfg["measurements"][n], workers=workers, calib_path=calib,
                              progress=_studya_progress)
        s["attempts"] = marker["attempts"]
        s["gates"] = [{"gate": g, "ok": ok, "message": msg} for g, ok, msg in R.gates(st)]
        R.save(s, rounds, R.RESULTS, n)
        _studya_print(s)


@studya_app.command("show")
def studya_show(name: str) -> None:
    from robustlab.studya import run as R

    p = R.RESULTS / f"{name}.json"
    if not p.exists():
        typer.echo(f"no result {p}", err=True)
        raise typer.Exit(1)
    _studya_print(json.loads(p.read_text(encoding="utf-8")))


@studya_app.command("fidelity-export")
def studya_fidelity_export(config: Path = STUDYA_CFG,
                           terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal"),
                           out_dir: Path | None = typer.Option(None, "--out-dir", help="Default: Common\\Files")) -> None:
    """Write the two synthetic M1-bar files for RL_ImportT1 (SPEC §3)."""
    from robustlab.config.loader import load_terminal_config
    from robustlab.studya import evaluate as E
    from robustlab.studya import fidelity as fd

    st = E.setup(config)
    if out_dir is None:
        out_dir = load_terminal_config(terminal).common_files_dir
    meta = fd.export(st, out_dir)
    for k in ("H0", "E1"):
        typer.echo(f"{k}: {meta[k]['symbol']} {meta[k]['rows']} M1 bars -> {meta[k]['file']} "
                   f"(price {meta[k]['price_min']:.5f}-{meta[k]['price_max']:.5f})")
    typer.echo(f"data from {meta['data_from']}, test from {meta['test_from']} to {meta['test_to_exclusive']} (exclusive)")
    typer.echo("next: run the script RL_ImportT1 in MT5 (it prints 'RL_ImportT1: done (... OK ... OK)')")


@studya_app.command("fidelity-run")
def studya_fidelity_run(config: Path = STUDYA_CFG,
                        terminal: Path = typer.Option(Path("configs/terminal.yaml"), "--terminal"),
                        partition: Path = typer.Option(Path("configs/data_partition.yaml"), "--partition"),
                        workspace: Path = typer.Option(Path("workspace_studya_fid"), "--workspace")) -> None:
    """Run the fidelity tests (12 regular + stop-out tests) on MT5 and compare them with the T1 model (t2).
    Saves results/fidelity_t1.json with the raw tester data (attach this one file)."""
    from robustlab.config.loader import load_terminal_config
    from robustlab.mt5 import telemetry_reader as tr
    from robustlab.single_backtest import run_backtest
    from robustlab.storage.artifacts import ArtifactStore
    from robustlab.studya import evaluate as E
    from robustlab.studya import fidelity as fd

    st = E.setup(config)
    acct_path = load_terminal_config(terminal).common_files_dir / fd.ACCOUNT_FILE
    acct = json.loads(acct_path.read_text(encoding="utf-8-sig")) if acct_path.exists() else None
    report: dict = {"definition": fd.DEFINITION, "config_hash": st.cfg_hash, "account_settings": acct, "tests": []}
    for t, req in fd.requests(st, workspace.resolve() / "studya_requests"):
        out = run_backtest(req, terminal, partition, workspace)
        item: dict = {"name": t.name, "symbol": fd.SYMBOLS[t.kind], "status": out.status, "run_id": out.run_id}
        report["tests"].append(item)
        # a stop-out test may end before the period end (MT5 stops the test): keep its raw data when the ONLY
        # problems are data coverage, and record that it ended early (SPEC §12.9)
        early = (t.purpose == "stop_out" and out.status == "QUARANTINED" and bool(out.message)
                 and all(m.strip().startswith("DATA_COVERAGE") for m in out.message.split(";")))
        if out.status not in ("SUCCEEDED", "ALREADY_SUCCEEDED") and not early:
            item["message"] = out.message
            typer.echo(f"{t.name}: {out.status} {out.message}")
            continue
        if early:
            item["ended_early"] = out.message
        db = _open(workspace)
        try:
            job = db.one("SELECT job_id FROM job WHERE run_id = ? AND status IN ('SUCCEEDED', 'QUARANTINED') "
                         "ORDER BY job_id DESC LIMIT 1", (out.run_id,))
            store = ArtifactStore(workspace.resolve() / "artifacts", db)

            def art(role: str) -> str:
                row = db.one("SELECT sha256 FROM job_artifact WHERE job_id = ? AND role = ?", (job["job_id"], role))
                return store.read(row["sha256"]).decode("utf-8-sig")

            deals = tr.parse_csv_text(art("telemetry_deals"), tr.DEALS_COLUMNS, "deals")
            daily = tr.parse_csv_text(art("telemetry_daily"), tr.DAILY_COLUMNS, "daily")
            env = json.loads(art("telemetry_env"))
            stats = json.loads(art("telemetry_stats")).get("values", {})
        finally:
            db.close()
        item["raw"] = fd.raw_of(deals, daily, env, stats.get("profit"))
        if early:
            item["raw"]["ended_early"] = out.message
    _studya_fidelity_report(st, report)


@studya_app.command("fidelity-check")
def studya_fidelity_check(config: Path = STUDYA_CFG,
                          report: Path | None = typer.Option(None, "--report", help="Default: results/fidelity_t1.json")) -> None:
    """Re-judge an attached fidelity_t1.json from its raw tester data with the current T1 model (no MT5 needed)."""
    from robustlab.studya import evaluate as E
    from robustlab.studya import run as R

    path = report or R.RESULTS / "fidelity_t1.json"
    if not path.exists():
        typer.echo(f"no report {path}", err=True)
        raise typer.Exit(1)
    _studya_fidelity_report(E.setup(config), json.loads(path.read_text(encoding="utf-8")))


def _studya_fidelity_report(st, report: dict) -> None:
    from robustlab.studya import fidelity as fd
    from robustlab.studya import run as R

    if report.get("config_hash") != st.cfg_hash:
        typer.echo("note: the report was made with another study_a.yaml (raw data re-judged with the current one)")
    rep = fd.check(st, report)
    for x in rep["tests"]:
        line = f"{x['name']}: deals model {x.get('deals_model')} tester {x.get('deals_tester')}"
        if "daily" in x:
            line += f"  daily max diff {x['daily']['max_abs_diff']}  SO {x['stop_out']['time']}"
        typer.echo(f"{line}  match: {x['match']}")
        for i in x["issues"][:5]:
            typer.echo(f"    {i}")
    for i in rep["account_issues"]:
        typer.echo(f"  {i}")
    typer.echo(f"implied stop-out bracket (margin level %): {rep['implied_stop_out_bracket']}")
    fd.save(rep, R.RESULTS / "fidelity_t1.json")
    typer.echo(f"=== fidelity {fd.DEFINITION}: {'MATCH (all ' + str(rep['n_tests']) + ' tests + settings)' if rep['match'] else 'NOT MATCHED'} ===")
    typer.echo(f"saved: {R.RESULTS / 'fidelity_t1.json'}")
    raise typer.Exit(0 if rep["match"] else 2)


if __name__ == "__main__":  # pragma: no cover
    app()
