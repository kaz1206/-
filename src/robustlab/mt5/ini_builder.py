"""Tester .ini and .set generation (ARCHITECTURE §14.2, P1_PLAN §15 F1).

Formats are exactly the ones confirmed on hardware in run R1 [S-HW-P1]:
- ini: ASCII, CRLF, [Tester] section only (no [Common], so no credentials).
- .set: UTF-16LE with BOM, CRLF; numeric/bool/enum/datetime as
  name=value||value||step||value||N, strings as name=value.
"""

from __future__ import annotations

import codecs
import datetime as dt

from robustlab.core.models import (
    CREDENTIAL_KEYS,
    MODEL_CODES,
    InputSpec,
    InputType,
    TesterSettings,
)

HARNESS_JOB_INPUT = "RL_JobId"

_STEP = {
    InputType.INT: "1",
    InputType.DOUBLE: "0.1",
    InputType.BOOL: "0",
    InputType.ENUM: "0",
    InputType.DATETIME: "0",
}


def _fmt(value: int | bool | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_set_text(inputs: list[InputSpec], normalized: dict[str, int | bool | str], job_id: str) -> str:
    lines = []
    for spec in inputs:
        v = _fmt(normalized[spec.name])
        if spec.type is InputType.STRING:
            lines.append(f"{spec.name}={v}")
        else:
            lines.append(f"{spec.name}={v}||{v}||{_STEP[spec.type]}||{v}||N")
    lines.append(f"{HARNESS_JOB_INPUT}={job_id}")
    return "\r\n".join(lines) + "\r\n"


def build_opt_set_text(inputs: list[InputSpec], fixed: dict[str, int | bool | str], axes, job_id: str) -> str:
    """Optimization .set: axes as name=start||start||step||stop||Y, the rest fixed with ||N (X9, [S-HW-P3])."""
    by_name = {a.name: a for a in axes}
    lines = []
    for spec in inputs:
        if spec.name in by_name:
            a = by_name[spec.name]
            lines.append(f"{spec.name}={a.start}||{a.start}||{a.step}||{a.stop}||Y")
            continue
        v = _fmt(fixed[spec.name])
        if spec.type is InputType.STRING:
            lines.append(f"{spec.name}={v}")
        else:
            lines.append(f"{spec.name}={v}||{v}||{_STEP[spec.type]}||{v}||N")
    lines.append(f"{HARNESS_JOB_INPUT}={job_id}")
    return "\r\n".join(lines) + "\r\n"


def encode_set(text: str) -> bytes:
    return codecs.BOM_UTF16_LE + text.encode("utf-16-le")


def _mt5_date(d: dt.date) -> str:
    return d.strftime("%Y.%m.%d")


def _number(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else format(x, "g")


def report_name(job_id: str) -> str:
    return f"RL_{job_id}_report"


def build_ini_text(
    *,
    expert: str,
    set_filename: str,
    symbol: str,
    timeframe: str,
    from_date: dt.date,
    to_date: dt.date,
    tester: TesterSettings,
    job_id: str,
    optimization: bool = False,
) -> str:
    # Optimization=1 is the full grid (X1). OptimizationCriterion is deliberately not set: the XML
    # "Result" column depends on it and is not used (C49).
    lines = [
        "[Tester]",
        f"Expert={expert}",
        f"ExpertParameters={set_filename}",
        f"Symbol={symbol}",
        f"Period={timeframe}",
        f"Model={MODEL_CODES[tester.model]}",
        f"ExecutionMode={tester.execution_delay_ms}",
        f"Optimization={1 if optimization else 0}",
        "ForwardMode=0",
        f"FromDate={_mt5_date(from_date)}",
        f"ToDate={_mt5_date(to_date)}",
        f"Deposit={_number(tester.deposit)}",
        f"Currency={tester.currency}",
        f"Leverage={tester.leverage}",
        "Visual=0",
        f"Report={report_name(job_id)}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "UseLocal=1",
        "UseRemote=0",
        "UseCloud=0",
    ]
    text = "\r\n".join(lines) + "\r\n"
    assert_safe_ini(text)
    return text


def assert_safe_ini(text: str) -> None:
    """The ini must be ASCII (W1) and must never carry credentials (P1_PLAN §13-7)."""
    if not text.isascii():
        raise ValueError("ini must be ASCII")
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.lower() != "[tester]":
            raise ValueError(f"unexpected ini section {line!r}")
        key = line.split("=", 1)[0].strip().lower()
        if key in CREDENTIAL_KEYS:
            raise ValueError(f"credential key {key!r} must not appear in the ini")
