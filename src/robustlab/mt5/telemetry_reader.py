"""Read and validate the P1 telemetry written by Telemetry.mqh (P1_PLAN §2.2, §15 F2/F4/F5).

A result is accepted only if every integrity check passes. Anything else is
TELEMETRY_MISSING (no completion marker) or QUARANTINED (marker present but
the data are inconsistent or unreadable).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from robustlab.core.models import TIMEFRAME_CODES, JobStatus, TesterSettings

DEALS_COLUMNS = [
    "ticket", "time", "time_msc", "type", "entry", "volume", "price", "profit",
    "commission", "swap", "fee", "symbol", "magic", "position_id", "reason", "comment",
]
TRADE_DEAL_TYPES = {0, 1}  # DEAL_TYPE_BUY, DEAL_TYPE_SELL
PROFIT_TOLERANCE = Decimal("0.01")
DEALS_SCHEMA_VERSION = "deals_v1"

FILE_ROLES = ("deals", "stats", "env", "done")
_SUFFIX = {"deals": "deals.csv", "stats": "stats.json", "env": "env.json", "done": "done.json"}


def telemetry_filename(job_id: str, role: str) -> str:
    return f"RL_{job_id}_{_SUFFIX[role]}"


def parse_mt5_time(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y.%m.%d %H:%M:%S")


@dataclass
class TelemetryResult:
    status: JobStatus
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)
    done: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    env: dict[str, Any] | None = None
    deals: list[dict[str, str]] | None = None
    canonical_deals: bytes | None = None
    deals_content_hash: str | None = None
    net_profit_sum: Decimal | None = None


@dataclass(frozen=True)
class Expectation:
    job_id: str
    telemetry_version: str
    symbol: str
    timeframe: str
    from_date: dt.date
    to_date: dt.date
    tester: TesterSettings
    coverage_tolerance_days: int


def locate(common_dir: Path, job_id: str) -> dict[str, Path]:
    return {r: common_dir / telemetry_filename(job_id, r) for r in FILE_ROLES}


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a JSON object")
    return data


def _read_deals(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header != DEALS_COLUMNS:
        raise ValueError(f"deals header mismatch: {header}")
    rows = []
    for i, rec in enumerate(reader, start=2):
        if not rec:
            continue
        if len(rec) != len(DEALS_COLUMNS):
            raise ValueError(f"deals line {i}: expected {len(DEALS_COLUMNS)} fields, got {len(rec)}")
        rows.append(dict(zip(DEALS_COLUMNS, rec, strict=True)))
    return rows


def canonical_deals_bytes(rows: list[dict[str, str]]) -> bytes:
    """E1/C26: canonical CSV (fixed columns, rows sorted by time_msc then ticket, LF, UTF-8)."""
    ordered = sorted(rows, key=lambda r: (int(r["time_msc"]), int(r["ticket"])))
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(DEALS_COLUMNS)
    for r in ordered:
        w.writerow([r[c].strip() if c != "comment" else r[c] for c in DEALS_COLUMNS])
    return buf.getvalue().encode("utf-8")


def deals_parquet_bytes(rows: list[dict[str, str]], content_hash: str) -> bytes:
    def ints(c):
        return pa.array([int(r[c]) for r in rows], pa.int64())

    def floats(c):
        return pa.array([float(r[c]) for r in rows], pa.float64())

    table = pa.table(
        {
            "ticket": ints("ticket"),
            "time": pa.array([r["time"] for r in rows], pa.string()),
            "time_msc": ints("time_msc"),
            "type": ints("type"),
            "entry": ints("entry"),
            "volume": floats("volume"),
            "price": floats("price"),
            "profit": floats("profit"),
            "commission": floats("commission"),
            "swap": floats("swap"),
            "fee": floats("fee"),
            "symbol": pa.array([r["symbol"] for r in rows], pa.string()),
            "magic": ints("magic"),
            "position_id": ints("position_id"),
            "reason": ints("reason"),
            "comment": pa.array([r["comment"] for r in rows], pa.string()),
        }
    ).replace_schema_metadata(
        {"robustlab.schema": DEALS_SCHEMA_VERSION, "robustlab.content_sha256": content_hash}
    )
    out = io.BytesIO()
    pq.write_table(table, out)
    return out.getvalue()


def _dec(v: Any, what: str) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{what}: not a number: {v!r}") from e


def validate(common_dir: Path, exp: Expectation) -> TelemetryResult:
    files = locate(common_dir, exp.job_id)
    present = {r: p for r, p in files.items() if p.is_file()}
    res = TelemetryResult(status=JobStatus.QUARANTINED, files=present)
    if "done" not in present:
        res.status = JobStatus.TELEMETRY_MISSING
        res.problems.append("completion marker not found")
        return res
    missing = [r for r in FILE_ROLES if r not in present]
    if missing:
        res.problems.append(f"telemetry files missing: {missing}")
        return res

    try:
        res.done = _read_json(present["done"])
        res.stats = _read_json(present["stats"])
        res.env = _read_json(present["env"])
        res.deals = _read_deals(present["deals"])
    except (ValueError, UnicodeDecodeError) as e:
        res.problems.append(f"PARSE: {e}")
        return res

    p = res.problems
    done, stats, env, deals = res.done, res.stats, res.env, res.deals
    try:
        # 1. marker belongs to this job and this telemetry version
        if done.get("job_id") != exp.job_id:
            p.append(f"marker job_id {done.get('job_id')!r} != {exp.job_id!r}")
        if str(done.get("telemetry_version")) != exp.telemetry_version:
            p.append(f"telemetry_version {done.get('telemetry_version')!r} != {exp.telemetry_version!r}")
        if env.get("job_id") != exp.job_id:
            p.append("env job_id mismatch")

        # 2. counts in the marker match the CSV
        trade_rows = [r for r in deals if int(r["type"]) in TRADE_DEAL_TYPES]
        if int(done.get("deals_total", -1)) != len(deals):
            p.append(f"deals_total {done.get('deals_total')} != CSV rows {len(deals)}")
        if int(done.get("trade_deals", -1)) != len(trade_rows):
            p.append(f"trade_deals {done.get('trade_deals')} != CSV trade rows {len(trade_rows)}")

        # 3. F4: trade deal count and P/L agree with TesterStatistics (no magic filter)
        sv = stats.get("values", {})
        if int(_dec(sv.get("deals"), "stats.deals")) != len(trade_rows):
            p.append(f"STAT_DEALS {sv.get('deals')} != trade deals {len(trade_rows)}")
        total = sum(
            (_dec(r[c], c) for r in trade_rows for c in ("profit", "commission", "swap", "fee")), Decimal(0)
        )
        res.net_profit_sum = total
        if abs(total - _dec(sv.get("profit"), "stats.profit")) > PROFIT_TOLERANCE:
            p.append(f"sum of trade deal P/L {total} != STAT_PROFIT {sv.get('profit')}")

        # 4. observed environment matches the request
        if env.get("mql_tester") is not True:
            p.append("telemetry was not produced inside the Strategy Tester")
        if env.get("symbol", {}).get("name") != exp.symbol:
            p.append(f"symbol {env.get('symbol', {}).get('name')!r} != {exp.symbol!r}")
        if int(env.get("period", -1)) != TIMEFRAME_CODES[exp.timeframe]:
            p.append(f"period {env.get('period')} != {exp.timeframe}")
        if env.get("account_currency") != exp.tester.currency:
            p.append(f"currency {env.get('account_currency')!r} != {exp.tester.currency!r}")
        if int(env.get("account_leverage", -1)) != exp.tester.leverage:
            p.append(f"leverage {env.get('account_leverage')} != {exp.tester.leverage}")
        if abs(_dec(env.get("initial_balance"), "initial_balance") - _dec(exp.tester.deposit, "deposit")) > PROFIT_TOLERANCE:
            p.append(f"initial balance {env.get('initial_balance')} != deposit {exp.tester.deposit}")

        # 5. F2: half-open period [from, to) and data coverage
        ticks = int(env.get("ticks", 0))
        if ticks <= 0:
            p.append("DATA_COVERAGE: no ticks were processed")
        else:
            first = parse_mt5_time(env["first_tick"])
            last = parse_mt5_time(env["last_tick"])
            start = dt.datetime.combine(exp.from_date, dt.time())
            end = dt.datetime.combine(exp.to_date, dt.time())
            tol = dt.timedelta(days=exp.coverage_tolerance_days)
            if first < start or last >= end:
                p.append(f"PERIOD_SEMANTICS: ticks {first}..{last} fall outside [{start}, {end})")
            if first > start + tol:
                p.append(f"DATA_COVERAGE: first tick {first} is more than {exp.coverage_tolerance_days} days after {start}")
            if last < end - tol:
                p.append(f"DATA_COVERAGE: last tick {last} is more than {exp.coverage_tolerance_days} days before {end}")
    except (KeyError, TypeError, ValueError) as e:
        p.append(f"PARSE: {e}")

    if not p:
        res.canonical_deals = canonical_deals_bytes(deals)
        res.deals_content_hash = hashlib.sha256(res.canonical_deals).hexdigest()
        res.status = JobStatus.SUCCEEDED
    return res
