"""Study A fidelity check (SPEC §3.1-3.4, comparison t1): does RL_TSMom trade like the T1 model on the SAME prices?

1. export: one H0 path and one E1 path (fixed seeds) -> M1-bar CSVs for two MT5 custom symbols (EURUSD_RLT1 = H0,
   EURUSD_RLT2 = E1; the first 6 letters set the FOREX currencies [S-HW-P10]) built by
   mql5/Scripts/RobustLab/RL_ImportT1.mq5 (spread fixed at 10 points, swaps set to 0).
2. run: 12 single tests (6 cells x 2 paths, H4, 1 minute OHLC, delay 0) in their own workspace, then compare every
   test's telemetry with the T1 model on the same prices (definition t1, fixed before the run; SPEC §3.3, §10 A10).
Dates are synthetic (2008-2012, far from the holdout). The P10 symbol EURUSD_RLH0 and its script are not touched.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.studya import evaluate as E
from robustlab.studya import prices, t1

TEST_START = dt.date(2010, 1, 4)     # a Monday
SYMBOLS = {"H0": "EURUSD_RLT1", "E1": "EURUSD_RLT2"}
FILES = {"H0": "RL_T1_H0_M1.csv", "E1": "RL_T1_E1_M1.csv"}
E1_SCALE = 2.5e-4                    # a visible trend for the comparison; the size does not matter here
SPREAD_POINTS = 10
POINT = 0.00001
DEFINITION = "t1"


def layout(st: E.Setup) -> tuple[list[dt.date], t1.Window]:
    f = st.cfg["fidelity"]
    first = prices.weekdays_before(TEST_START, int(f["pre_weekdays"]))
    days = prices.weekdays(first, TEST_START + dt.timedelta(days=int(f["test_weekdays"]) * 2))
    days = days[:int(f["pre_weekdays"]) + int(f["test_weekdays"])]
    return days, t1.Window(int(f["pre_weekdays"]) * st.spd, len(days) * st.spd)


def path_of(st: E.Setup, kind: str) -> np.ndarray:
    days, _ = layout(st)
    n_bars = len(days) * st.pm.bars_per_day
    seed = int(st.cfg["fidelity"]["seeds"][kind])
    steps = prices.base_steps(n_bars, st.pm, seed, 0)
    if kind == "E1":
        e = st.cfg["edges"]["E1"]
        steps = steps + E1_SCALE * prices.edge_unit(e["kind"], n_bars, st.pm.steps_per_bar, seed, 0,
                                                    persistence_bars=float(e["persistence_bars"]))
    return prices.path(steps, float(st.cfg["account"]["start_price"]))


def step_time(s: int, days: list[dt.date], spd: int) -> dt.datetime:
    return t1.step_time(s, days, spd)


def export(st: E.Setup, out_dir: Path) -> dict[str, Any]:
    days, w = layout(st)
    minutes = 24 * 60 // st.spd
    out = {}
    for kind in ("H0", "E1"):
        p = path_of(st, kind)
        path = out_dir / FILES[kind]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="\n") as fh:
            for s in range(1, len(p)):
                t0 = step_time(s, days, st.spd)
                px = f"{p[s]:.5f}"
                for m in range(minutes):
                    ts = int((t0 + dt.timedelta(minutes=m)).replace(tzinfo=dt.UTC).timestamp())
                    fh.write(f"{ts},{px},{px},{px},{px},1,{SPREAD_POINTS}\n")
        out[kind] = {"file": str(path), "symbol": SYMBOLS[kind], "rows": (len(p) - 1) * minutes,
                     "price_min": float(p.min()), "price_max": float(p.max())}
    out["data_from"] = days[0].isoformat()
    out["test_from"] = days[w.s_start // st.spd].isoformat()
    out["test_to_exclusive"] = (days[-1] + dt.timedelta(days=1)).isoformat()
    return out


def requests(st: E.Setup, req_dir: Path) -> list[tuple[str, int, int, Path]]:
    days, w = layout(st)
    out = []
    req_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("H0", "E1"):
        for L, H in st.cfg["fidelity"]["cells"]:
            req = {"strategy_file": str((E.REPO / "configs" / "strategies" / "rl_tsmom.yaml").resolve()),
                   "symbol": SYMBOLS[kind], "timeframe": "H4", "from_date": days[w.s_start // st.spd].isoformat(),
                   "to_date": (days[-1] + dt.timedelta(days=1)).isoformat(),
                   "params": {"InpLookback": int(L), "InpHoldBars": int(H), "InpLots": float(st.cfg["lots"])},
                   "tester": {"model": "OHLC_M1", "execution_delay_ms": 0, "deposit": 10000, "currency": "USD",
                              "leverage": 100},
                   "coverage_tolerance_days": 7}
            path = req_dir / f"fid_{kind}_{L}_{H}.yaml"
            path.write_text(yaml.safe_dump(req, sort_keys=True), encoding="utf-8")
            out.append((kind, int(L), int(H), path))
    return out


def model(st: E.Setup, kind: str, L: int, H: int) -> t1.TestResult:
    days, w = layout(st)
    a = st.cfg["account"]
    return t1.run_test(path_of(st, kind), w, L, H, lots=float(st.cfg["lots"]), spread=float(a["spread"]),
                       contract=float(a["contract"]), deposit=float(a["deposit"]), leverage=float(a["leverage"]),
                       spb=st.pm.steps_per_bar, spd=st.spd, days=days, symbol=SYMBOLS[kind])


def _parse_time(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y.%m.%d %H:%M:%S")


def compare(st: E.Setup, kind: str, res: t1.TestResult, tester_deals: list[dict[str, str]],
            env: dict[str, Any], tester_profit: float | None) -> dict[str, Any]:
    """Comparison t1 (SPEC §3.3 #1-#8, §10 A10)."""
    days, w = layout(st)
    p = path_of(st, kind)
    test_end = dt.datetime.combine(days[-1] + dt.timedelta(days=1), dt.time())
    last_step_start = step_time(w.s_end, days, st.spd)

    def rows(ds: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted((d for d in ds if d["type"] in ("0", "1")), key=lambda d: (d["time"], int(d["ticket"])))

    md, td = rows(res.deals), rows(tester_deals)
    is_end = lambda d: d.get("comment", "").strip('"') == t1.END_OF_TEST
    issues: list[str] = []
    n_ea = (sum(1 for d in md if not is_end(d)), sum(1 for d in td if not is_end(d)))
    n_end = (sum(1 for d in md if is_end(d)), sum(1 for d in td if is_end(d)))
    if n_ea[0] != n_ea[1]:
        issues.append(f"#1 EA deals model {n_ea[0]} vs tester {n_ea[1]}")
    if n_end[0] != n_end[1]:
        issues.append(f"#1 end-of-test deals model {n_end[0]} vs tester {n_end[1]}")
    profit_ea = profit_end = 0.0
    for i, (a, b) in enumerate(zip(md, td, strict=False)):
        ta, tb = _parse_time(a["time"]), _parse_time(b["time"])
        same = (a["type"] == b["type"] and a["entry"] == b["entry"] and abs(float(a["volume"]) - float(b["volume"])) < 1e-9
                and abs(float(a["price"]) - float(b["price"])) < 0.5 * POINT)
        if is_end(a) or is_end(b):
            ok_t = is_end(a) and is_end(b) and last_step_start <= tb <= test_end
        else:
            ok_t = 0 <= (tb - ta).total_seconds() < 60
        if not (same and ok_t):
            issues.append(f"#2 deal {i}: model {a['time']} {a['type']}/{a['entry']} {a['price']} | tester {b['time']} "
                          f"{b['type']}/{b['entry']} {b['price']} {b.get('comment', '')}")
        if a["entry"] == "1":
            dp = abs(float(a["profit"]) - float(b["profit"]))
            if dp >= 0.01:
                issues.append(f"#4{'b' if is_end(a) else 'a'} close {i}: profit model {a['profit']} vs tester {b['profit']}")
            if is_end(b):
                profit_end += float(b["profit"])
            else:
                profit_ea += float(b["profit"])
    if md and td and not (0 <= (_parse_time(td[0]["time"]) - _parse_time(md[0]["time"])).total_seconds() < 60):
        issues.append(f"#3 first deal model {md[0]['time']} vs tester {td[0]['time']}")
    swap = round(sum(float(d["swap"]) for d in td), 2)
    comm = round(sum(float(d["commission"]) + float(d["fee"]) for d in td), 2)
    if swap != 0.0:
        issues.append(f"#5 swap {swap}")
    if comm != 0.0:
        issues.append(f"#6 commission/fee {comm}")
    if tester_profit is not None and abs(round(tester_profit, 2) - round(profit_ea + profit_end + swap + comm, 2)) > 0.005:
        issues.append(f"#7 net {tester_profit} != EA closes {profit_ea:.2f} + end of test {profit_end:.2f}")
    # #8 spread: entry price - synthetic bid at that step
    bad_spread = 0
    for b in td:
        if b["entry"] != "0":
            continue
        tb = _parse_time(b["time"])
        day = days.index(tb.date())
        s = day * st.spd + (tb.hour * 60 + tb.minute) // (24 * 60 // st.spd) + 1
        want = SPREAD_POINTS * POINT if b["type"] == "0" else 0.0
        if abs(float(b["price"]) - p[s] - want) >= 0.5 * POINT:
            bad_spread += 1
    if bad_spread:
        issues.append(f"#8 spread: {bad_spread} entries not at bid + {SPREAD_POINTS} points (buy) / bid (sell)")
    sym = env.get("symbol", {})
    settings = {"spread_float": sym.get("spread_float"), "observed_spread": (env.get("observed") or {}).get("spread"),
                "swap_long": sym.get("swap_long"), "swap_short": sym.get("swap_short"), "symbol": sym.get("name")}
    if settings["spread_float"] not in (False, 0) or settings["swap_long"] not in (0, 0.0) or \
            settings["swap_short"] not in (0, 0.0):
        issues.append(f"#8/#5 symbol settings {settings}")
    return {"definition": DEFINITION, "deals_model": len(md), "deals_tester": len(td), "ea_deals": n_ea,
            "end_of_test_deals": n_end, "first_deal": [md[0]["time"] if md else None, td[0]["time"] if td else None],
            "profit_ea_closes": round(profit_ea, 2), "profit_end_of_test": round(profit_end, 2), "swap": swap,
            "commission_fee": comm, "tester_profit": tester_profit, "symbol_settings": settings,
            "issues": issues[:20], "n_issues": len(issues), "match": not issues}


def save(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
