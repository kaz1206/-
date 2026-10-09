"""Deterministic IDs and canonical serialization (ARCHITECTURE §8.1, P1_PLAN §2.3).

All content IDs are a prefix plus the first 16 hex digits of the sha256 of a
canonical JSON document, so the same content always yields the same ID.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from typing import Any

HARNESS_INPUT_PREFIX = "RL_"  # Telemetry inputs; excluded from candidate_id (C21)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_id(prefix: str, obj: Any) -> str:
    return f"{prefix}_{sha256_hex(canonical_json(obj))[:16]}"


def strategy_version_id(ex5_sha256: str, mq5_sha256: str | None, telemetry_version: str) -> str:
    """mq5 is optional so closed-source EAs can be registered (C20)."""
    return content_id(
        "sv",
        {"ex5_sha256": ex5_sha256, "mq5_sha256": mq5_sha256, "telemetry_version": telemetry_version},
    )


def candidate_id(strategy_version: str, symbol: str, timeframe: str, normalized_params: dict) -> str:
    for name in normalized_params:
        if name.startswith(HARNESS_INPUT_PREFIX):
            raise ValueError(f"harness input {name!r} must not be part of a candidate")
    return content_id(
        "cd",
        {
            "strategy_version_id": strategy_version,
            "symbol": symbol,
            "timeframe": timeframe,
            "params": normalized_params,
        },
    )


def tester_settings_hash(settings: dict) -> str:
    return sha256_hex(canonical_json(settings))


def run_id(
    candidate: str,
    from_date: dt.date,
    to_date: dt.date,
    settings_hash: str,
    cost_scenario: str = "BASE",
    source: str = "SINGLE_TEST",
) -> str:
    """P1 run identity: the period is the half-open [from_date, to_date) in server time (C19, C28)."""
    return content_id(
        "rn",
        {
            "candidate_id": candidate,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "tester_settings_hash": settings_hash,
            "cost_scenario": cost_scenario,
            "source": source,
        },
    )


def new_job_id(now: dt.datetime | None = None) -> str:
    """Time-sortable, unique per attempt: jb_<UTC yyyymmddHHMMSSmmm>_<8 hex>."""
    now = now or dt.datetime.now(dt.UTC)
    stamp = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    return f"jb_{stamp}_{secrets.token_hex(4)}"
