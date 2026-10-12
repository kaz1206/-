"""T1 archetype: the exact rules of mql5/Experts/RobustLab/RL_TSMom.mq5 v1.00 on a synthetic path (SPEC §2, §10).

Conventions (as verified on hardware for the P10 fidelity check [S-HW-P10]): step s (30 minutes) holds price p[s]
for its 30 M1 bars; H4 bar k (k >= 1) closes at p[k * spb]; the first tick of bar k + 1 is at step k * spb + 1,
where the EA decides and its market order fills (buy at ask = bid + spread, sell at bid). Equity marks longs at bid
and shorts at ask. One test = one window of steps with its own deposit and its own bar counter; all earlier steps of
the path are history (bars available to the EA).

RL_TSMom: on every new bar g_count++; decide when (g_count - 1) % H == 0 and Bars >= L + 2; c1 = close[1],
c0 = close[1 + L]; c1 > c0 -> long, c1 < c0 -> short, equal -> nothing; a different direction closes then opens.
The tester closes what is left at the end ("end of test"). Stop-out as in MT5: when the margin level falls to
stop_out_pct the position is closed at that tick (reason SO); later orders are refused while the balance is below the
required margin ("not enough money"). Telemetry records equity BEFORE the trade on the deciding
tick and after it on the next tick (same price inside a step): the day minimum includes both.
Output is in telemetry form (strings with the precision the EA writes) so that the production metric code reads it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import numpy as np

T1_VERSION = "t1a"
END_OF_TEST = "end of test"
MAGIC = 26101202  # RL_TSMOM_MAGIC


@dataclass(frozen=True)
class Window:
    """Steps (s_start, s_end]: the first tick is s_start + 1 (a day start), the last tick is s_end (a day end)."""
    s_start: int
    s_end: int


@dataclass
class TestResult:
    deals: list[dict[str, str]] = field(default_factory=list)     # telemetry deal rows (incl. the deposit)
    daily: list[dict[str, str]] = field(default_factory=list)     # telemetry daily rows
    stats: dict[str, float] = field(default_factory=dict)         # TesterStatistics as sent in frames
    tracking: dict[str, Any] = field(default_factory=dict)        # EA tracking values
    closes: list[dict[str, Any]] = field(default_factory=list)    # closing trades, EA vs end of test (trace)
    first_deal_step: int | None = None
    eq_close: np.ndarray | None = None                            # end-of-day equity (full=False only)


def step_time(s: int, days: list[dt.date], spd: int) -> dt.datetime:
    day, k = divmod(s - 1, spd)
    return dt.datetime.combine(days[day], dt.time()) + dt.timedelta(minutes=k * 24 * 60 // spd)


def _streaks(pnls: list[float]) -> tuple[int, float, int, float]:
    best_w = best_l = 0
    amt_w = amt_l = 0.0
    cw = cl = 0
    aw = al = 0.0
    for x in pnls:
        if x > 0:
            cw, aw, cl, al = cw + 1, aw + x, 0, 0.0
        elif x < 0:
            cl, al, cw, aw = cl + 1, al + x, 0, 0.0
        else:
            cw = cl = 0
            aw = al = 0.0
        if cw > best_w or (cw == best_w and aw > amt_w):
            best_w, amt_w = cw, aw
        if cl > best_l or (cl == best_l and al < amt_l):
            best_l, amt_l = cl, al
    return best_w, amt_w, best_l, amt_l


def run_test(p: np.ndarray, w: Window, lookback: int, hold: int, *, lots: float, spread: float, contract: float,
             deposit: float, leverage: float, spb: int, spd: int, days: list[dt.date], symbol: str = "EURUSD",
             full: bool = True, stop_out_pct: float = 50.0) -> TestResult:
    """One single test of RL_TSMom on prices p (5 digits) over window w. full=False: daily equity only (calibration)."""
    units = lots * contract
    k0 = w.s_start // spb
    ks = np.arange(k0, (w.s_end - 1) // spb + 1, hold)          # bars whose first tick is inside the window
    ks = ks[ks * spb + 1 <= w.s_end]
    ok = ks + 1 >= lookback + 2
    sig = np.zeros(len(ks), dtype=int)
    kk = ks[ok]
    c1, c0 = p[kk * spb], p[(kk - lookback) * spb]
    sig[ok] = np.sign(c1 - c0).astype(int)
    res = TestResult()
    n = w.s_end - w.s_start
    steps = np.arange(w.s_start + 1, w.s_end + 1)
    bid = p[steps]
    ask = np.round(bid + spread, 5)
    pos_arr = np.zeros(n, dtype=int)
    entry_arr = np.zeros(n)
    bal_arr = np.full(n, float(deposit))           # float even for an int deposit (else balances truncate)
    pre: list[tuple[int, float]] = []           # (step, equity before the trade) on deciding ticks
    bal = deposit
    entry = 0.0
    pos = 0
    pnls: list[float] = []
    stop_outs = 0
    tickets = 2
    if full:
        res.deals.append(_deal(1, step_time(w.s_start + 1, days, spd), 2, 0, 0.0, 0.0, deposit, symbol, 0, ""))

    def close(j: int, kind: str, comment: str, reason: str) -> None:
        nonlocal bal, pos, entry, tickets
        exit_px = float(bid[j]) if pos > 0 else float(ask[j])
        pnl = round((exit_px - entry) * pos * units, 2)
        bal = round(bal + pnl, 2)
        pnls.append(pnl)
        s = w.s_start + 1 + j
        res.closes.append({"step": s, "kind": kind, "side": -pos, "price": exit_px, "profit": pnl})
        if full:
            res.deals.append(_deal(tickets, step_time(s, days, spd), 1 if pos > 0 else 0, 1, lots, exit_px, pnl,
                                   symbol, MAGIC, comment, reason))
            tickets += 1
        pos, entry = 0, 0.0

    def hold_until(j0: int, j1: int) -> int | None:
        """Hold pos from step index j0 to j1 (inclusive); return the stop-out index if the margin level falls to
        the stop-out level (MT5 closes the position at that tick)."""
        if pos == 0 or j1 < j0:
            return None
        mk = bid[j0:j1 + 1] if pos > 0 else ask[j0:j1 + 1]
        eqs = bal + pos * (mk - entry) * units
        ml = eqs / (lots * contract * bid[j0:j1 + 1] / leverage) * 100.0
        hit = np.flatnonzero(ml <= stop_out_pct)
        return j0 + int(hit[0]) if len(hit) else None

    last_j = 0
    for k, new in zip(ks.tolist(), sig.tolist(), strict=True):
        if new == 0:
            continue                                     # equal closes (or no history yet): nothing happens
        s = k * spb + 1
        j = s - w.s_start - 1
        so = hold_until(last_j, j)
        if so is not None:
            pos_arr[last_j:so] = pos
            entry_arr[last_j:so] = entry
            bal_arr[last_j:so] = bal
            close(so, "STOP_OUT", "so", "6")
            stop_outs += 1
            last_j = so
        pos_arr[last_j:j] = pos
        entry_arr[last_j:j] = entry
        bal_arr[last_j:j] = bal
        if new == pos:
            last_j = j
            continue
        b, a = float(bid[j]), float(ask[j])
        pre.append((s, bal + (pos * ((b if pos > 0 else a) - entry) * units if pos else 0.0)))
        if pos:
            close(j, "EA", "", "3")
        price = a if new > 0 else b
        if bal >= lots * contract * price / leverage:      # MT5 refuses an order without enough free margin
            entry, pos = price, new
            if full:
                res.deals.append(_deal(tickets, step_time(s, days, spd), 0 if new > 0 else 1, 0, lots, entry, 0.0,
                                       symbol, MAGIC, "", "3"))
                tickets += 1
            if res.first_deal_step is None:
                res.first_deal_step = s
        last_j = j
    so = hold_until(last_j, n - 1)
    if so is not None:
        pos_arr[last_j:so] = pos
        entry_arr[last_j:so] = entry
        bal_arr[last_j:so] = bal
        close(so, "STOP_OUT", "so", "6")
        stop_outs += 1
        last_j = so
    pos_arr[last_j:] = pos
    entry_arr[last_j:] = entry
    bal_arr[last_j:] = bal
    # equity after any trade on every step (marks: long at bid, short at ask)
    mark = np.where(pos_arr > 0, bid, ask)
    floating = np.where(pos_arr != 0, pos_arr * (mark - entry_arr) * units, 0.0)
    eq = bal_arr + floating
    # end of test: the tester closes the open position at the last tick
    final_pos = int(pos_arr[-1]) if n else 0
    if final_pos:
        exit_px = float(bid[-1]) if final_pos > 0 else float(ask[-1])
        pnl = round((exit_px - float(entry_arr[-1])) * final_pos * units, 2)
        pnls.append(pnl)
        res.closes.append({"step": w.s_end, "kind": "END_OF_TEST", "side": -final_pos, "price": exit_px, "profit": pnl})
        if full:
            res.deals.append(_deal(tickets, step_time(w.s_end, days, spd), 1 if final_pos > 0 else 0, 1, lots,
                                   exit_px, pnl, symbol, MAGIC, END_OF_TEST, "0"))
    # daily rows (window is whole days)
    nd = n // spd
    eq_d = eq.reshape(nd, spd)
    day_min = eq_d.min(axis=1)
    day_max = eq_d.max(axis=1)
    for s, v in pre:
        d = (s - w.s_start - 1) // spd
        day_min[d] = min(day_min[d], v)
        day_max[d] = max(day_max[d], v)
    d0 = w.s_start // spd
    if not full:
        res.eq_close = eq_d[:, -1].copy()
        return res
    bal_d = bal_arr.reshape(nd, spd)[:, -1]
    pos_d = (pos_arr.reshape(nd, spd) != 0).max(axis=1)
    for d in range(nd):
        res.daily.append({"date": f"{days[d0 + d]:%Y.%m.%d}", "balance_close": f"{bal_d[d]:.2f}",
                          "equity_close": f"{eq_d[d, -1]:.2f}", "equity_min": f"{day_min[d]:.2f}",
                          "equity_max": f"{day_max[d]:.2f}", "positions_max": str(int(pos_d[d])),
                          "ticks": str(spd * 120)})
    # tracking (tick level: every step after the trade plus the pre-trade values)
    all_eq = np.concatenate([eq, np.array([v for _, v in pre])]) if pre else eq
    order_eq = _tick_order(eq, pre, w.s_start)
    peak = np.maximum.accumulate(np.concatenate([[deposit], order_eq]))[1:]
    dd = peak - order_eq
    i_dd = int(np.argmax(dd)) if len(dd) else 0
    held = pos_arr != 0
    margin = lots * contract * bid / leverage
    ml = np.where(held, eq / margin * 100.0, np.inf)
    res.tracking = {
        "equity_peak": round(float(max(deposit, all_eq.max())), 2),
        "equity_max_dd": round(float(dd.max()) if len(dd) else 0.0, 2),
        "equity_max_dd_pct": round(float(dd[i_dd] / peak[i_dd] * 100.0) if len(dd) and peak[i_dd] > 0 else 0.0, 4),
        "max_floating_loss": round(float(min(0.0, floating.min())), 2),
        "max_positions": int(held.any()), "max_lots": lots if held.any() else 0.0,
        "min_margin_level": round(float(ml.min()), 2) if held.any() else None,
        "stop_out_deals": stop_outs, "equity_min": round(float(all_eq.min()), 2),
    }
    gp = round(sum(x for x in pnls if x > 0), 2)
    gl = round(sum(x for x in pnls if x < 0), 2)
    cw, aw, cl, al = _streaks(pnls)
    balances = deposit + np.cumsum([0.0, *pnls])
    bpeak = np.maximum.accumulate(balances)
    res.stats = {"initial_deposit": deposit, "profit": round(sum(pnls), 2), "trades": float(len(pnls)),
                 "deals": float(sum(1 for d in res.deals if d["type"] in ("0", "1"))),
                 "profit_trades": float(sum(1 for x in pnls if x > 0)),
                 "loss_trades": float(sum(1 for x in pnls if x < 0)), "gross_profit": gp, "gross_loss": gl,
                 "balance_dd": round(float((bpeak - balances).max()), 2), "equity_dd": res.tracking["equity_max_dd"],
                 "profit_factor": (gp / -gl) if gl < 0 else 0.0,
                 "expected_payoff": (sum(pnls) / len(pnls)) if pnls else 0.0, "recovery_factor": 0.0,
                 "sharpe_ratio": 0.0, "max_conwins": round(aw, 2), "max_conprofit_trades": float(cw),
                 "max_conlosses": round(al, 2), "max_conloss_trades": float(cl)}
    return res


def _tick_order(eq: np.ndarray, pre: list[tuple[int, float]], s_start: int) -> np.ndarray:
    """Equity in tick order: on a deciding step the pre-trade value comes first."""
    if not pre:
        return eq
    out = []
    last = 0
    for s, v in pre:
        j = s - s_start - 1
        out.append(eq[last:j])
        out.append(np.array([v]))
        last = j
    out.append(eq[last:])
    return np.concatenate(out)


def _deal(ticket: int, t: dt.datetime, typ: int, entry: int, volume: float, price: float, profit: float,
          symbol: str, magic: int, comment: str, reason: str = "0") -> dict[str, str]:
    return {"ticket": str(ticket), "time": f"{t:%Y.%m.%d %H:%M:%S}", "time_msc": "0", "type": str(typ),
            "entry": str(entry), "volume": f"{volume:.2f}", "price": f"{price:.5f}", "profit": f"{profit:.2f}",
            "commission": "0.00", "swap": "0.00", "fee": "0.00", "symbol": symbol if typ != 2 else "",
            "magic": str(magic), "position_id": "0", "reason": reason, "comment": comment}


def daily_returns(res: TestResult, deposit: float) -> np.ndarray:
    eq = np.array([float(r["equity_close"]) for r in res.daily])
    return np.diff(np.concatenate([[deposit], eq])) / deposit
