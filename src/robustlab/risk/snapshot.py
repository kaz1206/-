"""Broker snapshot for P9 (P9_PLAN §4, C12). Windows only: needs a running terminal and the MetaTrader5 package.

Only the fields listed in ACCOUNT_FIELDS / SYMBOL_FIELDS are written. The account login, holder name, server name
and IP are never read into the snapshot. Money conversions come from order_calc_margin / order_calc_profit; there
is no own tick-value arithmetic.
API names: the functions exist in MetaTrader5 5.0.6231 [S-MT5PY] (A); argument order and return details are B
(the official pages are blocked from the cloud environment) and are checked on the terminal (FINDINGS §3).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from typing import Any

SNAPSHOT_VERSION = "snap1"
ACCOUNT_FIELDS = ("currency", "leverage", "margin_so_mode", "margin_so_call", "margin_so_so", "equity", "margin_mode")
SYMBOL_FIELDS = ("volume_min", "volume_step", "volume_max", "trade_contract_size", "currency_profit",
                 "currency_margin", "digits", "point")
STOPOUT_MODE_PERCENT, STOPOUT_MODE_MONEY = 0, 1  # ACCOUNT_STOPOUT_MODE_PERCENT / _MONEY


TICK_WAIT_SEC, TICK_POLL_SEC = 10.0, 0.5  # a symbol just added to Market Watch gets its first price after a moment


class SnapshotError(RuntimeError):
    pass


def _price(mt5: Any, symbol: str, wait: float = TICK_WAIT_SEC, poll: float = TICK_POLL_SEC) -> Any:
    """The current bid/ask, waiting up to `wait` seconds for the first tick of a freshly selected symbol."""
    deadline = time.monotonic() + wait
    while True:
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None and tick.ask and tick.bid:
            return tick
        if time.monotonic() >= deadline:
            got = "nothing" if tick is None else f"bid={tick.bid} ask={tick.ask}"
            raise SnapshotError(f"no price for {symbol} after {wait:.0f} s (symbol_info_tick returned {got}; "
                                f"last_error {mt5.last_error()}). Check that the terminal is connected to the demo "
                                f"account and that {symbol} shows prices in Market Watch, then run again.")
        time.sleep(poll)


def take(mt5: Any, symbol: str, width: float) -> dict[str, Any]:
    """mt5 = the MetaTrader5 module (or a test double); width = cost-stress price width of the symbol."""
    if not mt5.initialize():
        raise SnapshotError(f"initialize failed: {mt5.last_error()}")
    try:
        acc = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if acc is None or info is None:
            raise SnapshotError(f"account_info / symbol_info returned nothing for {symbol}: {mt5.last_error()}")
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise SnapshotError(f"cannot add {symbol} to Market Watch: {mt5.last_error()}")
        tick = _price(mt5, symbol)
        m_buy = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, 1.0, tick.ask)
        m_sell = mt5.order_calc_margin(mt5.ORDER_TYPE_SELL, symbol, 1.0, tick.bid)
        cost = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, tick.ask, tick.ask + width)
        if m_buy is None or m_sell is None or cost is None:
            raise SnapshotError(f"order_calc_* returned nothing: {mt5.last_error()}")
        snap = {
            "version": SNAPSHOT_VERSION, "symbol": symbol,
            "taken_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "account": {k: getattr(acc, k) for k in ACCOUNT_FIELDS},
            "symbol_info": {k: getattr(info, k) for k in SYMBOL_FIELDS},
            "price": {"bid": tick.bid, "ask": tick.ask},
            "margin_per_lot": max(float(m_buy), float(m_sell)), "margin_buy": float(m_buy),
            "margin_sell": float(m_sell),
            "stress_width": width, "stress_cost_per_lot": abs(float(cost)),
        }
    finally:
        mt5.shutdown()
    validate(snap)
    return snap


def validate(snap: dict[str, Any]) -> None:
    for k in ("account", "symbol_info", "margin_per_lot", "stress_cost_per_lot", "symbol"):
        if k not in snap:
            raise SnapshotError(f"snapshot misses {k}")
    extra = set(snap["account"]) - set(ACCOUNT_FIELDS)
    if extra:
        raise SnapshotError(f"snapshot has account fields that are not allowed: {sorted(extra)}")
    si = snap["symbol_info"]
    if not (si["volume_min"] > 0 and si["volume_step"] > 0 and si["volume_max"] >= si["volume_min"]):
        raise SnapshotError("bad volume_min / volume_step / volume_max")
    if snap["account"]["margin_so_mode"] not in (STOPOUT_MODE_PERCENT, STOPOUT_MODE_MONEY):
        raise SnapshotError(f"unknown margin_so_mode {snap['account']['margin_so_mode']}")
    if not snap["account"]["equity"] > 0 or not snap["margin_per_lot"] > 0:
        raise SnapshotError("equity and margin per lot must be > 0")


def load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    snap = json.loads(raw)
    validate(snap)
    return snap, hashlib.sha256(raw).hexdigest()


def save(snap: dict[str, Any], path: Path) -> str:
    raw = json.dumps(snap, indent=2, sort_keys=True).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()
