"""Readers for the outputs of an MT5 optimization (P3_PLAN §3, C40-C41, C46-C49, [S-HW-P3]).

Three independent sources are read and cross-checked:
- the tester log (Tester/logs): completion line, agent breakdown, cache hits
- the Telemetry v3 frame files written by the frame-mode EA instance (source of truth, C41)
- the optimization XML report (SpreadsheetML), matched by column names only (C49)
"""

from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from robustlab.mt5.telemetry_reader import parse_csv_text

# --- tester log (K1-K3) ----------------------------------------------------
OPT_STARTED = "complete optimization started"
OPT_FINISHED_RE = re.compile(r"optimization finished, total passes (\d+)")
CACHE_HIT_PATTERN = "optimization already processed"
AGENTS_RE = re.compile(r"local (\d+) tasks \(\d+%\), remote (\d+) tasks \(\d+%\), cloud (\d+) tasks")


@dataclass
class TesterLogFacts:
    started: bool
    finished_passes: int | None
    cache_hit: bool
    local_tasks: int | None
    remote_tasks: int | None
    cloud_tasks: int | None


def read_tester_log(text: str) -> TesterLogFacts:
    fin = OPT_FINISHED_RE.findall(text)
    agents = AGENTS_RE.findall(text)
    loc, rem, cld = (int(x) for x in agents[-1]) if agents else (None, None, None)
    return TesterLogFacts(
        started=OPT_STARTED in text,
        finished_passes=int(fin[-1]) if fin else None,
        cache_hit=CACHE_HIT_PATTERN in text,
        local_tasks=loc,
        remote_tasks=rem,
        cloud_tasks=cld,
    )


# --- frame files (Telemetry v3) --------------------------------------------
FRAME_FORMAT = 3
PASS_DAILY_COLUMNS = ["pass", "date", "balance_close", "equity_close", "equity_min", "equity_max",
                      "positions_max", "ticks"]
FRAME_TRACKING_KEYS = ("equity_peak", "equity_min", "equity_max_dd", "equity_max_dd_pct", "max_floating_loss",
                       "max_positions", "max_lots", "min_margin_level", "stop_out_deals")
FRAME_STAT_KEYS = ("initial_deposit", "profit", "gross_profit", "gross_loss", "trades", "deals", "balance_dd",
                   "equity_dd", "profit_factor", "expected_payoff", "recovery_factor", "sharpe_ratio",
                   "profit_trades", "loss_trades", "max_conwins", "max_conprofit_trades", "max_conlosses",
                   "max_conloss_trades")
_SUFFIX = {"fm_init": "fm_init.json", "passes": "passes.jsonl", "pdaily": "pdaily.csv", "fm_done": "fm_done.json"}


def frame_filename(job_id: str, role: str) -> str:
    return f"RL_{job_id}_{_SUFFIX[role]}"


def locate_frames(common_dir: Path, job_id: str) -> dict[str, Path]:
    return {r: common_dir / frame_filename(job_id, r) for r in _SUFFIX}


@dataclass
class PassFrame:
    pass_no: int
    inputs: dict[str, str]  # as reported by FrameInputs
    stats: dict[str, float]
    tracking: dict[str, Any]
    ticks: int
    first_tick: str
    last_tick: str
    daily: list[dict[str, str]] = field(default_factory=list)


@dataclass
class FrameData:
    init: dict[str, Any]
    done: dict[str, Any]
    passes: list[PassFrame]


class FrameError(ValueError):
    pass


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise FrameError(f"{path.name}: not a JSON object")
    return data


def read_frames(files: dict[str, Path], job_id: str) -> FrameData:
    """Parse the frame-mode outputs. Raises FrameError on any structural problem."""
    missing = [r for r, p in files.items() if not p.is_file()]
    if missing:
        raise FrameError(f"frame files missing: {missing}")
    try:
        init, done = _json(files["fm_init"]), _json(files["fm_done"])
        lines = [ln for ln in files["passes"].read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        records = [json.loads(ln) for ln in lines]
        daily_rows = parse_csv_text(files["pdaily"].read_text(encoding="utf-8-sig"), PASS_DAILY_COLUMNS, "pdaily")
    except (ValueError, UnicodeDecodeError) as e:
        raise FrameError(f"PARSE: {e}") from e
    for doc, what in ((init, "fm_init"), (done, "fm_done")):
        if doc.get("job_id") != job_id:
            raise FrameError(f"{what} job_id {doc.get('job_id')!r} != {job_id!r}")
        if str(doc.get("telemetry_version")) != str(FRAME_FORMAT):
            raise FrameError(f"{what} telemetry_version {doc.get('telemetry_version')!r} != '{FRAME_FORMAT}'")
    if done.get("mql_frame_mode", True) is not True or init.get("mql_frame_mode") is not True:
        raise FrameError("frame files were not written by the optimization frame-mode instance")
    by_pass: dict[int, list[dict[str, str]]] = {}
    for r in daily_rows:
        by_pass.setdefault(int(r["pass"]), []).append(r)
    passes = []
    for rec in records:
        try:
            if int(rec["format"]) != FRAME_FORMAT:
                raise FrameError(f"pass {rec.get('pass')}: frame format {rec['format']} != {FRAME_FORMAT}")
            inputs = {}
            for item in rec["inputs"]:
                name, _, value = str(item).partition("=")
                inputs[name.strip()] = value.strip()
            stats = {k: float(rec["stats"][k]) for k in FRAME_STAT_KEYS}
            tracking = {k: rec["tracking"][k] for k in FRAME_TRACKING_KEYS}
            pf = PassFrame(int(rec["pass"]), inputs, stats, tracking, int(rec["ticks"]), str(rec["first_tick"]),
                           str(rec["last_tick"]), sorted(by_pass.get(int(rec["pass"]), []), key=lambda r: r["date"]))
            if int(rec["days"]) != len(pf.daily):
                raise FrameError(f"pass {pf.pass_no}: {rec['days']} days announced, {len(pf.daily)} daily rows")
        except (KeyError, TypeError, ValueError) as e:
            if isinstance(e, FrameError):
                raise
            raise FrameError(f"PARSE pass record: {e}") from e
        passes.append(pf)
    return FrameData(init, done, passes)


def check_pass(pf: PassFrame, *, deposit: float, from_date: dt.date, to_date: dt.date,
               tolerance_days: int) -> list[str]:
    """Per-pass integrity checks (the same ideas as v2 single tests: F2 and the daily series)."""
    p = []
    tag = f"pass {pf.pass_no}"
    if abs(pf.stats["initial_deposit"] - deposit) > 0.01:
        p.append(f"{tag}: initial deposit {pf.stats['initial_deposit']} != {deposit}")
    if float(pf.tracking["equity_max_dd"]) < 0:
        p.append(f"{tag}: equity_max_dd is negative")
    if pf.ticks <= 0:
        p.append(f"{tag}: DATA_COVERAGE: no ticks were processed")
    else:
        first = dt.datetime.strptime(pf.first_tick, "%Y.%m.%d %H:%M:%S")
        last = dt.datetime.strptime(pf.last_tick, "%Y.%m.%d %H:%M:%S")
        start = dt.datetime.combine(from_date, dt.time())
        end = dt.datetime.combine(to_date, dt.time())
        tol = dt.timedelta(days=tolerance_days)
        if first < start or last >= end:
            p.append(f"{tag}: PERIOD_SEMANTICS: ticks {first}..{last} fall outside [{start}, {end})")
        if first > start + tol or last < end - tol:
            p.append(f"{tag}: DATA_COVERAGE: ticks {first}..{last} do not cover [{start}, {end}) within {tolerance_days} days")
    if not pf.daily:
        p.append(f"{tag}: DAILY: the daily series is empty")
        return p
    dates = [dt.datetime.strptime(r["date"], "%Y.%m.%d").date() for r in pf.daily]
    if len(set(dates)) != len(dates):
        p.append(f"{tag}: DAILY: duplicate dates")
    if dates[0] < from_date or dates[-1] >= to_date:
        p.append(f"{tag}: DAILY: dates {dates[0]}..{dates[-1]} fall outside [{from_date}, {to_date})")
    final = float(pf.daily[-1]["equity_close"])
    if abs(final - (deposit + pf.stats["profit"])) > 0.01:
        p.append(f"{tag}: DAILY: final equity {final} != deposit + profit {deposit + pf.stats['profit']:.2f}")
    return p


# --- optimization XML report (C49) -----------------------------------------
_SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
XML_REQUIRED = ("Pass", "Profit", "Trades")


class XmlReportError(ValueError):
    pass


def read_opt_report(data: bytes, axis_names: list[str]) -> list[dict[str, str]]:
    """Rows of the first worksheet keyed by header name. Only the columns we rely on are required."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise XmlReportError(f"XML_UNREADABLE: {e}") from e
    rows = []
    for row in root.iter(f"{_SS}Row"):
        rows.append([(d.text or "").strip() for d in row.iter(f"{_SS}Data")])
    if not rows:
        raise XmlReportError("XML_UNREADABLE: no rows")
    header = rows[0]
    need = [*XML_REQUIRED, *axis_names]
    absent = [c for c in need if c not in header]
    if absent:
        raise XmlReportError(f"XML_UNREADABLE: columns not found: {absent} (header {header})")
    out = []
    for r in rows[1:]:
        if len(r) != len(header):
            raise XmlReportError(f"XML_UNREADABLE: row with {len(r)} cells, header has {len(header)}")
        out.append(dict(zip(header, r, strict=True)))
    return out
