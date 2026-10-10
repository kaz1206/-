"""MetricEngine m1 (P2_PLAN §3, docs/metrics_definitions.md).

Pure functions over normalized inputs; no MT5 or I/O dependency. Trade-based definitions
were matched against the MT5 report on hardware data (P2_PLAN §1.1).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats as sstats

METRIC_DEF_VERSION = "m1"

DEAL_BUY, DEAL_SELL, DEAL_BALANCE = 0, 1, 2
ENTRY_IN, ENTRY_OUT, ENTRY_INOUT, ENTRY_OUT_BY = 0, 1, 2, 3
CLOSING_ENTRIES = {ENTRY_OUT, ENTRY_INOUT, ENTRY_OUT_BY}


@dataclass
class MetricResult:
    metrics: dict[str, Any]
    unavailable: dict[str, str]  # metric -> reason it is null
    periods: list[dict[str, Any]] = field(default_factory=list)


def _pnl(d: dict[str, str]) -> float:
    return float(d["profit"]) + float(d["commission"]) + float(d["swap"]) + float(d["fee"])


def _time(d: dict[str, str]) -> dt.datetime:
    return dt.datetime.strptime(d["time"], "%Y.%m.%d %H:%M:%S")


def _ordered(deals: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(deals, key=lambda d: (int(d["time_msc"]), int(d["ticket"])))


def _streaks(pnls: list[float]) -> tuple[int, float, int, float]:
    """Longest win run (count, amount of that run) and loss run. Zero P/L breaks both runs."""
    best_w = best_l = cur_w = cur_l = 0
    amt_w = amt_l = run_w = run_l = 0.0
    for x in pnls:
        if x > 0:
            cur_w, run_w, cur_l, run_l = cur_w + 1, run_w + x, 0, 0.0
        elif x < 0:
            cur_l, run_l, cur_w, run_w = cur_l + 1, run_l + x, 0, 0.0
        else:
            cur_w = cur_l = 0
            run_w = run_l = 0.0
        if cur_w > best_w:
            best_w, amt_w = cur_w, run_w
        if cur_l > best_l:
            best_l, amt_l = cur_l, run_l
    return best_w, amt_w, best_l, amt_l


def longest_runs(deals: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """Every run of maximal length, for wins and for losses (diagnostics for MT5 tie-breaking)."""
    closes = [d for d in _ordered(deals) if int(d["type"]) in (DEAL_BUY, DEAL_SELL)
              and int(d["entry"]) in CLOSING_ENTRIES]
    out: dict[str, list[dict[str, Any]]] = {}
    for label, sign in (("wins", 1), ("losses", -1)):
        runs, cur = [], []
        for d in closes + [None]:
            if d is not None and _pnl(d) * sign > 0:
                cur.append(d)
                continue
            if cur:
                runs.append(cur)
            cur = []
        best = max((len(r) for r in runs), default=0)
        out[label] = [{"length": len(r), "amount": round(sum(_pnl(d) for d in r), 2),
                       "first": r[0]["time"], "last": r[-1]["time"]} for r in runs if len(r) == best]
        top = max(runs, key=lambda r: sum(_pnl(d) for d in r) * sign, default=[])
        out[f"most_{label}_amount"] = [{"length": len(top), "amount": round(sum(_pnl(d) for d in top), 2)}] if top else []
    return out


def _balance_drawdown(points: list[tuple[dt.datetime, float]]) -> tuple[float, float, float]:
    """Max drawdown (abs, pct of the peak at that moment) and the longest peak-to-recovery span in days."""
    peak = points[0][1]
    peak_t = points[0][0]
    max_dd = max_dd_pct = 0.0
    longest = 0.0
    for t, b in points:
        if b >= peak:
            longest = max(longest, (t - peak_t).total_seconds() / 86400)
            peak, peak_t = b, t
        else:
            dd = peak - b
            if dd > max_dd:
                max_dd, max_dd_pct = dd, (dd / peak * 100 if peak > 0 else math.nan)
    longest = max(longest, (points[-1][0] - peak_t).total_seconds() / 86400) if points[-1][1] < peak else longest
    return max_dd, max_dd_pct, longest


def compute(
    deals: list[dict[str, str]],
    *,
    initial_deposit: float,
    daily: list[dict[str, str]] | None = None,
    tracked: dict[str, Any] | None = None,
) -> MetricResult:
    m: dict[str, Any] = {}
    na: dict[str, str] = {}
    deals = _ordered(deals)
    trade_deals = [d for d in deals if int(d["type"]) in (DEAL_BUY, DEAL_SELL)]
    closes = [d for d in trade_deals if int(d["entry"]) in CLOSING_ENTRIES]
    pnls = [_pnl(d) for d in closes]

    # --- trades (definitions matched to the MT5 report, P2_PLAN §1.1) ---
    m["net_profit"] = round(sum(_pnl(d) for d in trade_deals), 2)
    m["trades"] = len(closes)
    m["inout_deals"] = sum(1 for d in closes if int(d["entry"]) == ENTRY_INOUT)
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    m["win_trades"], m["loss_trades"] = len(wins), len(losses)
    m["gross_profit"] = round(sum(wins), 2)
    m["gross_loss"] = round(sum(losses), 2)
    if closes:
        m["win_rate"] = len(wins) / len(closes)
        m["expected_payoff"] = sum(pnls) / len(closes)
    else:
        na["win_rate"] = na["expected_payoff"] = "no closed trades"
    m["avg_win"] = sum(wins) / len(wins) if wins else None
    m["avg_loss"] = sum(losses) / len(losses) if losses else None
    if not wins:
        na["avg_win"] = "no winning trades"
    if not losses:
        na["avg_loss"] = "no losing trades"
    if losses:
        m["profit_factor"] = sum(wins) / -sum(losses)
    else:
        na["profit_factor"] = "no losing trades"
    w, wa, lo, la = _streaks(pnls)
    m["max_consec_wins"], m["max_consec_wins_amount"] = w, round(wa, 2)
    m["max_consec_losses"], m["max_consec_losses_amount"] = lo, round(la, 2)
    vols = sorted({float(d["volume"]) for d in trade_deals})
    m["distinct_volumes"] = vols
    m["fixed_lot"] = len(vols) <= 1

    # --- balance curve and drawdown ---
    t0 = _time(deals[0]) if deals else dt.datetime(1970, 1, 1)
    bal = initial_deposit
    points = [(t0, bal)]
    for d in deals:
        typ = int(d["type"])
        if typ in (DEAL_BUY, DEAL_SELL):
            bal += _pnl(d)
            points.append((_time(d), bal))
    m["final_balance"] = round(bal, 2)
    dd, dd_pct, dd_days = _balance_drawdown(points)
    m["balance_max_dd"] = round(dd, 2)
    m["balance_max_dd_pct"] = dd_pct
    m["balance_max_dd_days"] = dd_days

    # --- EA-tracked equity values (telemetry v2) ---
    keys = ("equity_peak", "equity_max_dd", "equity_max_dd_pct", "max_floating_loss", "max_positions",
            "max_lots", "min_margin_level", "stop_out_deals")
    if tracked and all(k in tracked for k in keys):
        for k in keys:
            m[k] = tracked[k]
        m["equity_max_dd"] = round(float(tracked["equity_max_dd"]), 2)
        m["bankrupt"] = float(tracked.get("equity_min", 1)) <= 0
        if m["equity_max_dd"] > 0:
            m["recovery_factor"] = m["net_profit"] / m["equity_max_dd"]
        else:
            na["recovery_factor"] = "no equity drawdown"
    else:
        for k in (*keys, "bankrupt", "recovery_factor"):
            na[k] = "not tracked by this telemetry version (v2 needed)"

    # --- daily series (telemetry v2) ---
    if daily:
        closes_eq = [float(r["equity_close"]) for r in daily]
        pnl_d = np.diff(np.array([initial_deposit, *closes_eq]))
        ret = pnl_d / initial_deposit  # fixed lot, no compounding (ARCHITECTURE §12.1)
        m["days"] = len(ret)
        m["daily_mean_return"] = float(ret.mean())
        if len(ret) >= 2:
            sd = float(ret.std(ddof=1))
            m["daily_std_return"] = sd
            if sd > 0:
                m["daily_sharpe"] = float(ret.mean()) / sd
            else:
                na["daily_sharpe"] = "zero variance"
        else:
            na["daily_std_return"] = na["daily_sharpe"] = "fewer than 2 days"
        if len(ret) >= 3 and float(ret.std()) > 0:
            m["daily_skewness"] = float(sstats.skew(ret, bias=True))
            m["daily_kurtosis"] = float(sstats.kurtosis(ret, fisher=False, bias=True))  # not excess (PSR)
        else:
            na["daily_skewness"] = na["daily_kurtosis"] = "fewer than 3 days or zero variance"
    else:
        for k in ("days", "daily_mean_return", "daily_std_return", "daily_sharpe", "daily_skewness", "daily_kurtosis"):
            na[k] = "no daily series (telemetry v2 needed)"

    for k, v in list(m.items()):
        if isinstance(v, float) and not math.isfinite(v):
            m[k] = None
            na[k] = "not finite"
    for k in na:
        m.setdefault(k, None)
    return MetricResult(m, na, _periods(deals, initial_deposit))


def _periods(deals: list[dict[str, str]], initial_deposit: float) -> list[dict[str, Any]]:
    out = []
    for bucket_type, key in (("YEAR", "%Y"), ("MONTH", "%Y-%m")):
        groups: dict[str, list[dict[str, str]]] = {}
        for d in deals:
            if int(d["type"]) in (DEAL_BUY, DEAL_SELL):
                groups.setdefault(_time(d).strftime(key), []).append(d)
        bal = initial_deposit
        for k in sorted(groups):
            ds = groups[k]
            start = bal
            pts = [(_time(ds[0]), start)]
            for d in ds:
                bal += _pnl(d)
                pts.append((_time(d), bal))
            dd, _, _ = _balance_drawdown(pts)
            out.append({
                "bucket_type": bucket_type, "bucket_key": k,
                "net_profit": round(bal - start, 2),
                "trades": sum(1 for d in ds if int(d["entry"]) in CLOSING_ENTRIES),
                "balance_max_dd": round(dd, 2),
            })
    return out
