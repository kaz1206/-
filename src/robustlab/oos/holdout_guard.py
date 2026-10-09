"""Minimal Holdout Guard for P1 (P1_PLAN §2.4, ARCHITECTURE §7.2).

- There is no way to unlock the holdout in P1 (no OOS stage exists).
- The partition definition is pinned on first use; any later change rejects all runs.
- The overlap check treats the request as a closed interval [from, to] even though
  MT5 excludes ToDate (W12). This is deliberately conservative (C28).
- A rejection is a normal decision, not an exception (C24). Every decision is logged.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from robustlab.core.models import DataPartition
from robustlab.storage.db import Database, now_iso


@dataclass(frozen=True)
class GuardDecision:
    approved: bool
    reason: str


def check_overlap(from_date: dt.date, to_date: dt.date, partition: DataPartition) -> GuardDecision:
    embargo = dt.timedelta(days=partition.embargo_days)
    lo = partition.holdout_from - embargo
    hi = partition.holdout_to + embargo
    if from_date <= hi and to_date >= lo:
        return GuardDecision(
            False,
            f"HOLDOUT_OVERLAP: requested {from_date}..{to_date} touches holdout+embargo {lo}..{hi}",
        )
    return GuardDecision(True, "OK")


def evaluate(
    db: Database,
    *,
    request_hash: str,
    from_date: dt.date,
    to_date: dt.date,
    partition: DataPartition,
    partition_hash: str,
) -> GuardDecision:
    with db.tx():
        row = db.one("SELECT partition_hash FROM partition_registry WHERE id = 1")
        if row is None:
            db.insert(
                "partition_registry",
                {
                    "id": 1,
                    "partition_hash": partition_hash,
                    "partition_json": json.dumps(partition.model_dump(mode="json"), sort_keys=True),
                    "registered_at": now_iso(),
                },
            )
            decision = check_overlap(from_date, to_date, partition)
        elif row["partition_hash"] != partition_hash:
            decision = GuardDecision(
                False,
                "PARTITION_CHANGED: the holdout definition differs from the one pinned in this workspace "
                f"({row['partition_hash'][:12]} != {partition_hash[:12]}); use a new workspace",
            )
        else:
            decision = check_overlap(from_date, to_date, partition)
        db.insert(
            "holdout_access_log",
            {
                "requested_at": now_iso(),
                "request_hash": request_hash,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "partition_hash": partition_hash,
                "approved": int(decision.approved),
                "reason": decision.reason,
            },
        )
    return decision
