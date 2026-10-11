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
class OosAccess:
    """The only way to open the holdout (P8b, P8B_PLAN §3): one locked manifest, its candidates, the exact period."""

    manifest_id: str
    candidates: tuple[str, ...]


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


def evaluate_oos(db: Database, *, request_hash: str, from_date: dt.date, to_date: dt.date, partition: DataPartition,
                 partition_hash: str, access: OosAccess) -> GuardDecision:
    """Approve a single test on the holdout only for a locked manifest whose holdout this workspace has burned for
    it, and only for exactly [holdout_from, holdout_to + 1 day). Every decision is logged."""
    with db.tx():
        reg = db.one("SELECT partition_hash FROM partition_registry WHERE id = 1")
        man = db.one("SELECT partition_hash FROM oos_manifest WHERE manifest_id = ?", (access.manifest_id,))
        burned = db.one("SELECT manifest_id FROM holdout_state WHERE partition_hash = ? AND state = 'BURNED' "
                        "ORDER BY changed_at LIMIT 1", (partition_hash,))
        want = (partition.holdout_from, partition.holdout_to + dt.timedelta(days=1))
        if reg is None or reg["partition_hash"] != partition_hash:
            decision = GuardDecision(False, "OOS_REJECTED: the partition is not the one pinned in this workspace")
        elif man is None or man["partition_hash"] != partition_hash:
            decision = GuardDecision(False, f"OOS_REJECTED: no locked manifest {access.manifest_id} for this partition")
        elif burned is None or burned["manifest_id"] != access.manifest_id:
            decision = GuardDecision(False, "OOS_REJECTED: the holdout is not burned for this manifest")
        elif (from_date, to_date) != want:
            decision = GuardDecision(False, f"OOS_REJECTED: the period must be exactly {want[0]}..{want[1]} (exclusive)")
        else:
            decision = GuardDecision(True, f"OOS: manifest {access.manifest_id}")
        db.insert("holdout_access_log", {
            "requested_at": now_iso(), "request_hash": request_hash, "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(), "partition_hash": partition_hash, "approved": int(decision.approved),
            "reason": decision.reason})
    return decision
