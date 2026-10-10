"""rlab CLI for P1: backtest run / backtest show / jobs list (P1_PLAN §3, §5).

Exit codes: 0 ok, 1 configuration/input error, 2 runtime failure, 3 holdout guard rejection.
Log contents are never printed (they contain the account login and IP address, F8).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from robustlab.single_backtest import run_backtest
from robustlab.storage.db import Database

app = typer.Typer(no_args_is_help=True, add_completion=False, help="RobustLab (P1: single MT5 backtests)")
backtest = typer.Typer(no_args_is_help=True, help="Run and inspect single backtests")
jobs_app = typer.Typer(no_args_is_help=True, help="Inspect jobs")
app.add_typer(backtest, name="backtest")
app.add_typer(jobs_app, name="jobs")
metrics_app = typer.Typer(no_args_is_help=True, help="Compute and inspect metrics (P2)")
app.add_typer(metrics_app, name="metrics")

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
        sql = "SELECT job_id, run_id, attempt_no, status, observed_build, deals_count, error_class FROM job"
        params: tuple = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        rows = [dict(r) for r in db.all(sql + " ORDER BY job_id DESC LIMIT ?", (*params, limit))]
    finally:
        db.close()
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    for r in rows:
        typer.echo(f"{r['job_id']} {r['run_id']} #{r['attempt_no']} {r['status']} build={r['observed_build']} "
                   f"deals={r['deals_count']}" + (f" error={r['error_class']}" if r["error_class"] else ""))


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
        sm = metrics_store.load(db, job)
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


if __name__ == "__main__":  # pragma: no cover
    app()
