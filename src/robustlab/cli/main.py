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
                                    "received_passes", "observed_build", "error_class", "error_detail")}
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
                   f"agents={j['tester_log']}")
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


if __name__ == "__main__":  # pragma: no cover
    app()
