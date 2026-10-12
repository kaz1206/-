"""P10 fidelity check (P10_PLAN §4, Q3 = A): does the real EA trade like its Python model on the SAME H0 prices?

1. export: one H0 path (seed fixed) -> M1 bars CSV for an MT5 custom symbol (mql5/Scripts/RobustLab/RL_ImportH0.mq5)
   and a single-test request on that symbol (H4, OHLC M1). Step s (30 minutes) sets the price of its 30 M1 bars
   (flat bars), so H4 bar k closes at path[8k] and the next bar's first tick is path[8k + 1].
2. compare: the job's deals vs the exact rules of RL_SmokeTest (SMA cross on the last two closed bars, checked on
   the first tick of a new bar; close the open position, then buy at ask / sell at bid; fixed lot).
Dates are in 2011-2012 (synthetic data, far from the holdout). The data start PRE_DAYS weekdays before the test period
so that the tester has its bar history; the comparison covers the test period only. This checks the model of the EA, not the null itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from robustlab.nulltest import sim

FIDELITY_SEED = 20261014
START = dt.datetime(2012, 1, 2)  # a Monday: the test period starts here
PRE_DAYS = 30  # weekdays of history before START: the tester needs about 100 bars before the test start
#                (found on the terminal 2026-10-12: without them it moved the start to 2012-01-25)
SYMBOL = "EURUSD_RLH0"  # first 6 letters = EURUSD: a FOREX custom symbol takes its currencies from its name
SPREAD_POINTS = 10
POINT = 0.00001


def data_start() -> dt.date:
    d, n = START.date(), 0
    while n < PRE_DAYS:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def weekdays(n_days: int) -> list[dt.date]:
    """PRE_DAYS weekdays of history, then the n_days weekdays of the test period."""
    out, d = [], data_start()
    while len(out) < PRE_DAYS + n_days:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def path_of(n_days: int, pm: sim.PriceModel, start_price: float, seed: int = FIDELITY_SEED) -> np.ndarray:
    """History (PRE_DAYS) plus the test period (n_days), one continuous path."""
    return sim.prices(1, PRE_DAYS + n_days, np.random.default_rng([seed, 99]), pm, start_price)[0]


def step_time(s: int, days: list[dt.date], steps_per_day: int) -> dt.datetime:
    """Start time of step s (s >= 1): its price holds for the 30 minutes from here."""
    day, k = divmod(s - 1, steps_per_day)
    return dt.datetime.combine(days[day], dt.time()) + dt.timedelta(minutes=k * 24 * 60 // steps_per_day)


def export(out_csv: Path, n_days: int, pm: sim.PriceModel, start_price: float) -> dict[str, Any]:
    path = path_of(n_days, pm, start_price)
    days = weekdays(n_days)
    spd = pm.bars_per_day * pm.steps_per_bar
    minutes = 24 * 60 // spd
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="\n") as f:
        for s in range(1, len(path)):
            t0 = step_time(s, days, spd)
            px = f"{round(path[s], 5):.5f}"
            for m in range(minutes):
                ts = int((t0 + dt.timedelta(minutes=m)).replace(tzinfo=dt.UTC).timestamp())
                f.write(f"{ts},{px},{px},{px},{px},1,{SPREAD_POINTS}\n")
    return {"rows": (len(path) - 1) * minutes, "data_from": days[0].isoformat(), "test_from": START.date().isoformat(),
            "to_exclusive": (days[-1] + dt.timedelta(days=1)).isoformat(), "symbol": SYMBOL}


CONTRACT = 100_000.0
END_OF_TEST = "end of test"


@dataclass(frozen=True)
class Deal:
    time: dt.datetime
    side: str      # "buy" / "sell"
    entry: str     # "in" / "out"
    price: float
    profit: float = 0.0   # closing deals: (exit - entry) * direction * lots * contract, rounded to cents
    comment: str = ""


def smoketest_model(path: np.ndarray, n_days: int, fast: int, slow: int, pm: sim.PriceModel,
                    lots: float = 0.01) -> list[Deal]:
    """The exact RL_SmokeTest rules on the exported prices (5-digit rounding as in the CSV)."""
    p = np.round(path, 5)
    spb, spd = pm.steps_per_bar, pm.bars_per_day * pm.steps_per_bar
    days = weekdays(n_days)
    closes = p[::spb]            # closes[j] = close of H4 bar j (j >= 1)
    K = len(closes) - 1
    spread = SPREAD_POINTS * POINT

    def sma(j: int, n: int) -> float | None:
        return float(closes[j - n + 1:j + 1].mean()) if j - n + 1 >= 1 else None

    deals: list[Deal] = []
    pos = 0
    entry = 0.0
    first = PRE_DAYS * spd       # the EA starts flat at the first tick of the test period (step first + 1)
    for k in range(2, K):        # first tick of bar k + 1, last closed bars k - 1 and k
        if k * spb < first:
            continue
        f0, f1, s0, s1 = sma(k - 1, fast), sma(k, fast), sma(k - 1, slow), sma(k, slow)
        if None in (f0, f1, s0, s1):
            continue
        up, down = f0 <= s0 and f1 > s1, f0 >= s0 and f1 < s1
        if not (up or down):
            continue
        s = k * spb + 1
        bid = float(p[s])
        t = step_time(s, days, spd)
        ask = round(bid + spread, 5)
        if pos > 0:
            deals.append(Deal(t, "sell", "out", bid, round((bid - entry) * lots * CONTRACT, 2)))
        elif pos < 0:
            deals.append(Deal(t, "buy", "out", ask, round((entry - ask) * lots * CONTRACT, 2)))
        if up:
            deals.append(Deal(t, "buy", "in", ask))
            pos, entry = 1, ask
        else:
            deals.append(Deal(t, "sell", "in", bid))
            pos, entry = -1, bid
    return deals


def compare(model: list[Deal], tester: list[dict[str, str]]) -> dict[str, Any]:
    """tester: telemetry deal rows (buy/sell only). Match deal by deal in time order."""
    from robustlab.metrics.engine import DEAL_BUY, DEAL_SELL, ENTRY_IN
    from robustlab.mt5.telemetry_reader import parse_mt5_time

    rows = sorted((d for d in tester if int(d["type"]) in (DEAL_BUY, DEAL_SELL)),
                  key=lambda d: (d["time"], int(d["ticket"])))
    got = [Deal(parse_mt5_time(d["time"]), "buy" if int(d["type"]) == DEAL_BUY else "sell",
                "in" if int(d["entry"]) == ENTRY_IN else "out", float(d["price"])) for d in rows]
    mism = []
    for i in range(max(len(model), len(got))):
        a = model[i] if i < len(model) else None
        b = got[i] if i < len(got) else None
        same = (a and b and a.time == b.time and a.side == b.side and a.entry == b.entry
                and abs(a.price - b.price) < 0.5 * POINT)
        if not same:
            mism.append({"i": i, "model": a and a.__dict__ | {"time": str(a.time)},
                         "tester": b and b.__dict__ | {"time": str(b.time)}})
    # diagnosis only (the verdict above stays the strict, pre-registered comparison)
    pairs = list(zip(model, got, strict=False))
    same = [a.side == b.side and a.entry == b.entry and abs(a.price - b.price) < 0.5 * POINT for a, b in pairs]
    offsets = sorted({int((b.time - a.time).total_seconds()) for a, b in pairs})
    extra = [{"time": d["time"], "type": d["type"], "entry": d["entry"], "price": d["price"], "comment": d["comment"]}
             for d in rows[len(model):]]
    diagnosis = {"pairs": len(pairs), "same_side_entry_price": int(sum(same)), "time_offsets_seconds": offsets[:10],
                 "extra_tester_deals": extra[:5],
                 "tester_profit_sum": round(sum(float(d["profit"]) for d in rows), 2),
                 "tester_profit_nonzero_deals": sum(1 for d in rows if float(d["profit"]) != 0.0)}
    return {"model_deals": len(model), "tester_deals": len(got), "mismatches": len(mism), "first": mism[:5],
            "match": not mism, "diagnosis": diagnosis}


def spread_cost(model: list[Deal], lots: float = 0.01) -> float:
    """Spread paid by the model: one spread per opened position (buys at ask, sells at bid)."""
    return round(sum(1 for d in model if d.entry == "in") * SPREAD_POINTS * POINT * lots * CONTRACT, 2)


def compare_v2(model: list[Deal], tester: list[dict[str, str]]) -> dict[str, Any]:
    """Comparison v2 (P10_PLAN §8.3, fixed BEFORE the re-verification): same order, side, entry; price within 0.5
    point; time difference in [0, 60) seconds (same M1 bar); the tester's own end-of-test close is not an EA
    decision and is reported apart; each closing deal's profit (without swap / commission) within 0.01."""
    from robustlab.metrics.engine import DEAL_BUY, DEAL_SELL, ENTRY_IN
    from robustlab.mt5.telemetry_reader import parse_mt5_time

    rows = sorted((d for d in tester if int(d["type"]) in (DEAL_BUY, DEAL_SELL)),
                  key=lambda d: (d["time"], int(d["ticket"])))
    eot = [d for d in rows if (d.get("comment") or "").strip().strip('"') == END_OF_TEST]
    ea = [d for d in rows if d not in eot]
    got = [Deal(parse_mt5_time(d["time"]), "buy" if int(d["type"]) == DEAL_BUY else "sell",
                "in" if int(d["entry"]) == ENTRY_IN else "out", float(d["price"]), float(d["profit"])) for d in ea]
    deal_mism, profit_mism = [], []
    for i in range(max(len(model), len(got))):
        a = model[i] if i < len(model) else None
        b = got[i] if i < len(got) else None
        ok = (a is not None and b is not None and a.side == b.side and a.entry == b.entry
              and abs(a.price - b.price) < 0.5 * POINT and 0 <= (b.time - a.time).total_seconds() < 60)
        if not ok:
            deal_mism.append({"i": i, "model": a and str(a), "tester": b and str(b)})
        elif a.entry == "out" and abs(a.profit - b.profit) >= 0.01:
            profit_mism.append({"i": i, "model_profit": a.profit, "tester_profit": b.profit})
    closes_m = [d.profit for d in model if d.entry == "out"]
    closes_t = [d.profit for d in got if d.entry == "out"]
    return {"definition": "v2", "model_deals": len(model), "tester_ea_deals": len(got),
            "deal_mismatches": len(deal_mism), "first_deal_mismatches": deal_mism[:5],
            "profit_mismatches": len(profit_mism), "first_profit_mismatches": profit_mism[:5],
            "closed_profit_model": round(sum(closes_m), 2), "closed_profit_tester": round(sum(closes_t), 2),
            "spread_cost_model": spread_cost(model),
            "end_of_test": [{"time": d["time"], "price": d["price"], "profit": d["profit"]} for d in eot],
            "swap_tester": round(sum(float(d["swap"]) for d in rows), 2),
            "commission_tester": round(sum(float(d["commission"]) + float(d["fee"]) for d in rows), 2),
            "match": not deal_mism and not profit_mism}
