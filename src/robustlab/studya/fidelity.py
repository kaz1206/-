"""Study A fidelity check (SPEC §3.1-3.4, comparison t2 = t1 + §12.2-12.3): does RL_TSMom trade like the T1 model on
the SAME prices?

1. export: one H0 path and one E1 path (fixed seeds) -> M1-bar CSVs for two MT5 custom symbols (EURUSD_RLT1 = H0,
   EURUSD_RLT2 = E1; the first 6 letters set the FOREX currencies [S-HW-P10]) built by
   mql5/Scripts/RobustLab/RL_ImportT1.mq5 (spread fixed at 10 points, swaps set to 0). The script also writes the
   account / symbol margin settings (stop-out mode and level, calc mode, margin rates; no login, name or server) to
   Common\\Files\\RL_T1_account.json.
2. run (the user, on the MT5 PC): 12 regular single tests (6 cells x 2 paths, deposit 10,000) and 3 stop-out tests
   (small TEST deposits, same lots and EA rules), H4, 1 minute OHLC, delay 0, in their own workspace. The RAW tester
   data (deals, daily rows, a symbol/account subset of env, tracking) and the account settings go into
   results/fidelity_t1.json together with the comparison.
3. check (anyone, any OS): `rlab studya fidelity-check` recomputes the comparison from the raw data with the current
   T1 model, so that a model fix is re-judged on the same tester data without re-running MT5.
Dates are synthetic (2008-2012, far from the holdout). The P10 symbol EURUSD_RLH0 and its script are not touched.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from robustlab.studya import evaluate as E
from robustlab.studya import prices, t1

TEST_START = dt.date(2010, 1, 4)     # a Monday
SYMBOLS = {"H0": "EURUSD_RLT1", "E1": "EURUSD_RLT2"}
FILES = {"H0": "RL_T1_H0_M1.csv", "E1": "RL_T1_E1_M1.csv"}
ACCOUNT_FILE = "RL_T1_account.json"
E1_SCALE = 2.5e-4                    # a visible trend for the comparison; the size does not matter here
SPREAD_POINTS = 10
POINT = 0.00001
DEFINITION = "t2"
DAILY_TOL = 0.01                     # USD, end-of-day equity (SPEC §12.2)
MARGIN_LEVEL_TOL = 0.015             # percentage points: 2-decimal rounding of both sides (SPEC §12.3)
ENV_KEYS = ("account_currency", "account_leverage", "account_margin_mode", "initial_balance", "terminal_build",
            "symbol", "observed", "tracking")   # never account_server / account_company


@dataclass(frozen=True)
class FidTest:
    name: str
    kind: str          # H0 / E1 (which custom symbol)
    lookback: int
    hold: int
    deposit: float
    purpose: str       # regular / stop_out


def tests(st: E.Setup) -> list[FidTest]:
    f = st.cfg["fidelity"]
    dep = float(st.cfg["account"]["deposit"])
    out = [FidTest(f"{k}_{L}_{H}", k, int(L), int(H), dep, "regular") for k in ("H0", "E1") for L, H in f["cells"]]
    for t in f.get("stop_out_tests", []):
        L, H = t["cell"]
        out.append(FidTest(f"SO_{t['kind']}_{L}_{H}_{int(t['deposit'])}", t["kind"], int(L), int(H),
                           float(t["deposit"]), "stop_out"))
    return out


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


def requests(st: E.Setup, req_dir: Path) -> list[tuple[FidTest, Path]]:
    days, w = layout(st)
    out = []
    req_dir.mkdir(parents=True, exist_ok=True)
    for t in tests(st):
        req = {"strategy_file": str((E.REPO / "configs" / "strategies" / "rl_tsmom.yaml").resolve()),
               "symbol": SYMBOLS[t.kind], "timeframe": "H4", "from_date": days[w.s_start // st.spd].isoformat(),
               "to_date": (days[-1] + dt.timedelta(days=1)).isoformat(),
               "params": {"InpLookback": t.lookback, "InpHoldBars": t.hold, "InpLots": float(st.cfg["lots"])},
               "tester": {"model": "OHLC_M1", "execution_delay_ms": 0, "deposit": t.deposit, "currency": "USD",
                          "leverage": int(st.cfg["account"]["leverage"])},
               "coverage_tolerance_days": 7}
        path = req_dir / f"fid_{t.name}.yaml"
        path.write_text(yaml.safe_dump(req, sort_keys=True), encoding="utf-8")
        out.append((t, path))
    return out


def model(st: E.Setup, t: FidTest) -> t1.TestResult:
    days, w = layout(st)
    a = st.cfg["account"]
    return t1.run_test(path_of(st, t.kind), w, t.lookback, t.hold, lots=float(st.cfg["lots"]),
                       spread=float(a["spread"]), contract=float(a["contract"]), deposit=t.deposit,
                       leverage=float(a["leverage"]), spb=st.pm.steps_per_bar, spd=st.spd, days=days,
                       symbol=SYMBOLS[t.kind], stop_out_pct=float(a["stop_out_pct"]))


def raw_of(deals: list[dict[str, str]], daily: list[dict[str, str]], env: dict[str, Any],
           profit: float | None) -> dict[str, Any]:
    """What the report keeps from one tester run (no log text, no account login / name / server)."""
    return {"deals": deals, "daily": [{k: r[k] for k in ("date", "balance_close", "equity_close")} for r in daily],
            "env": {k: env.get(k) for k in ENV_KEYS}, "profit": profit}


def _parse_time(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y.%m.%d %H:%M:%S")


def _so_bracket(st: E.Setup, t: FidTest, res: t1.TestResult) -> list[float] | None:
    """Margin levels (%) that bound the stop-out level if the tester stops out on the model's tick: the level at
    that tick (<= level) and the lowest level on the ticks before it (> level). None without a model stop-out."""
    so = next((c for c in res.closes if c["kind"] == "STOP_OUT"), None)
    if so is None:
        return None
    a = st.cfg["account"]
    p = path_of(st, t.kind)
    before = t.deposit + sum(c["profit"] for c in res.closes if c["step"] < so["step"])
    margin = float(st.cfg["lots"]) * float(a["contract"]) * float(p[so["step"]]) / float(a["leverage"])
    return [round((before + so["profit"]) / margin * 100.0, 4), res.tracking["min_margin_level"]]


def compare(st: E.Setup, t: FidTest, res: t1.TestResult, raw: dict[str, Any]) -> dict[str, Any]:
    """Comparison t2 (SPEC §3.3 #1-#8, §10 A10, §12.2 #9, §12.3 #10-#13) of one test."""
    days, w = layout(st)
    p = path_of(st, t.kind)
    acc = st.cfg["account"]
    tester_deals, env, tester_profit = raw["deals"], raw["env"] or {}, raw["profit"]
    test_end = dt.datetime.combine(days[-1] + dt.timedelta(days=1), dt.time())
    last_step_start = step_time(w.s_end, days, st.spd)

    def rows(ds: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted((d for d in ds if d["type"] in ("0", "1")), key=lambda d: (d["time"], int(d["ticket"])))

    md, td = rows(res.deals), rows(tester_deals)
    is_end = lambda d: d.get("comment", "").strip('"') == t1.END_OF_TEST
    is_so = lambda d: d.get("reason") == "6"
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
                and abs(float(a["price"]) - float(b["price"])) < 0.5 * POINT and is_so(a) == is_so(b))
        if is_end(a) or is_end(b):
            ok_t = is_end(a) and is_end(b) and last_step_start <= tb <= test_end
        else:
            ok_t = 0 <= (tb - ta).total_seconds() < 60
        if not (same and ok_t):
            issues.append(f"#2 deal {i}: model {a['time']} {a['type']}/{a['entry']} {a['price']} reason {a['reason']} | "
                          f"tester {b['time']} {b['type']}/{b['entry']} {b['price']} reason {b.get('reason')} "
                          f"{b.get('comment', '')}")
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
    sym = env.get("symbol") or {}
    settings = {"spread_float": sym.get("spread_float"), "observed_spread": (env.get("observed") or {}).get("spread"),
                "swap_long": sym.get("swap_long"), "swap_short": sym.get("swap_short"), "symbol": sym.get("name")}
    if settings["spread_float"] not in (False, 0) or settings["swap_long"] not in (0, 0.0) or \
            settings["swap_short"] not in (0, 0.0):
        issues.append(f"#8/#5 symbol settings {settings}")
    # #9 daily equity: same days, end-of-day equity within 0.01 USD (SPEC §12.2)
    mdd = {r["date"]: float(r["equity_close"]) for r in res.daily}
    tdd = {r["date"]: float(r["equity_close"]) for r in raw["daily"]}
    if set(mdd) != set(tdd):
        only_m, only_t = sorted(set(mdd) - set(tdd)), sorted(set(tdd) - set(mdd))
        issues.append(f"#9 daily dates differ: model only {len(only_m)} {only_m[:3]}, tester only {len(only_t)} {only_t[:3]}")
    common = sorted(set(mdd) & set(tdd))
    diffs = [round(abs(mdd[d] - tdd[d]), 2) for d in common]
    bad_days = [d for d, x in zip(common, diffs, strict=True) if x > DAILY_TOL]
    if bad_days:
        d0 = bad_days[0]
        issues.append(f"#9 daily equity: {len(bad_days)} days off by > {DAILY_TOL} (first {d0}: model {mdd[d0]:.2f} "
                      f"vs tester {tdd[d0]:.2f})")
    # #10 stop-out count and reason; #11 nothing after a stop-out (orders refused)
    trk = env.get("tracking") or {}
    so_m, so_t = sum(1 for d in md if is_so(d)), sum(1 for d in td if is_so(d))
    so_trk = trk.get("stop_out_deals")
    if not (so_m == so_t == (int(so_trk) if so_trk is not None else -1)):
        issues.append(f"#10 stop-out deals model {so_m} vs tester deals {so_t} vs tester tracking {so_trk}")
    if t.purpose == "stop_out" and so_m == 0:
        issues.append("#10 stop-out test without a stop-out in the model (test setting invalid)")
    for name, ds in (("model", md), ("tester", td)):
        first_so = next((d["time"] for d in ds if is_so(d)), None)
        if first_so is not None and any(d["time"] > first_so for d in ds):
            issues.append(f"#11 {name}: deals after the stop-out at {first_so}")
    # #12 margin calculation: the lowest margin level the EA saw (stop-out tests: counted; regular: reported)
    ml_m, ml_t = res.tracking.get("min_margin_level"), trk.get("min_margin_level")
    ml_ok = (ml_m is None and ml_t is None) or (ml_m is not None and ml_t is not None
                                                and abs(float(ml_m) - float(ml_t)) <= MARGIN_LEVEL_TOL)
    if t.purpose == "stop_out" and not ml_ok:
        issues.append(f"#12 min margin level model {ml_m} vs tester {ml_t}")
    # #13 currency conversion and account settings seen by the EA in the tester
    want = {"account_currency": "USD", "account_leverage": int(acc["leverage"]), "currency_profit": "USD",
            "currency_margin": "EUR", "contract_size": float(acc["contract"]), "tick_size": float(acc["tick_size"]),
            "tick_value": float(acc["tick_value"]), "initial_balance": t.deposit}
    got = {"account_currency": env.get("account_currency"),
           "account_leverage": int(env["account_leverage"]) if env.get("account_leverage") is not None else None,
           "currency_profit": sym.get("currency_profit"), "currency_margin": sym.get("currency_margin"),
           "contract_size": sym.get("contract_size"), "tick_size": sym.get("tick_size"),
           "tick_value": sym.get("tick_value"), "initial_balance": env.get("initial_balance")}
    bad = {k: (got[k], v) for k, v in want.items()
           if not (got[k] == v or (isinstance(v, float) and got[k] is not None and abs(float(got[k]) - v) < 1e-9))}
    if bad:
        issues.append(f"#13 settings (tester, expected): {bad}")
    return {"definition": DEFINITION, "name": t.name, "kind": t.kind, "purpose": t.purpose, "deposit": t.deposit,
            "InpLookback": t.lookback, "InpHoldBars": t.hold, "deals_model": len(md), "deals_tester": len(td),
            "ea_deals": n_ea, "end_of_test_deals": n_end,
            "first_deal": [md[0]["time"] if md else None, td[0]["time"] if td else None],
            "profit_ea_closes": round(profit_ea, 2), "profit_end_of_test": round(profit_end, 2), "swap": swap,
            "commission_fee": comm, "tester_profit": tester_profit, "symbol_settings": settings,
            "daily": {"days_model": len(mdd), "days_tester": len(tdd), "max_abs_diff": max(diffs) if diffs else None,
                      "days_over_tol": len(bad_days)},
            "stop_out": {"model": so_m, "tester": so_t, "tester_tracking": so_trk,
                         "time": [next((d["time"] for d in md if is_so(d)), None),
                                  next((d["time"] for d in td if is_so(d)), None)],
                         "bracket_model": _so_bracket(st, t, res)},
            "min_margin_level": {"model": ml_m, "tester": ml_t, "match": ml_ok}, "settings": got,
            "issues": issues[:20], "n_issues": len(issues), "match": not issues}


def check_account(st: E.Setup, acct: dict[str, Any] | None) -> list[str]:
    """#14 stop-out level and margin rules from RL_ImportT1's settings file (SPEC §12.3). Missing = not confirmed."""
    if not acct:
        return [f"#14 {ACCOUNT_FILE} missing: the stop-out level is NOT confirmed (run RL_ImportT1 v1.01)"]
    out = []
    a = acct.get("account") or {}
    if a.get("so_mode") != "ACCOUNT_STOPOUT_MODE_PERCENT" or a.get("so_so") is None or \
            abs(float(a["so_so"]) - float(st.cfg["account"]["stop_out_pct"])) > 1e-9:
        out.append(f"#14 stop-out setting {a.get('so_mode')} {a.get('so_so')} (expected percent "
                   f"{st.cfg['account']['stop_out_pct']})")
    if a.get("trade_mode") != "ACCOUNT_TRADE_MODE_DEMO":
        out.append(f"#14 account trade mode {a.get('trade_mode')} (demo account only)")
    for name in SYMBOLS.values():
        s = (acct.get("symbols") or {}).get(name)
        if not s:
            out.append(f"#14 {name}: no symbol settings")
            continue
        bad = {k: s.get(k) for k, v in (("calc_mode", "SYMBOL_CALC_MODE_FOREX"), ("margin_initial", 0.0),
                                       ("margin_rate_buy_initial", 1.0), ("margin_rate_sell_initial", 1.0),
                                       ("contract_size", float(st.cfg["account"]["contract"])),
                                       ("currency_margin", "EUR"), ("currency_profit", "USD"))
               if s.get(k) != v and not (isinstance(v, float) and isinstance(s.get(k), (int, float))
                                         and abs(float(s[k]) - v) < 1e-9)}
        if bad:
            out.append(f"#14 {name}: margin settings {bad}")
    return out


def check(st: E.Setup, report: dict[str, Any]) -> dict[str, Any]:
    """Re-judge a report from its raw tester data with the CURRENT model (definition t2)."""
    want = tests(st)
    by_name = {t["name"]: t for t in report.get("tests", []) if "name" in t}
    out_tests, issues = [], []
    for t in want:
        item = by_name.get(t.name)
        if item is None or "raw" not in item:
            out_tests.append({"name": t.name, "match": False, "status": (item or {}).get("status"),
                              "issues": [f"not run / no raw tester data: {(item or {}).get('message')}"]})
            continue
        cmp = compare(st, t, model(st, t), item["raw"])
        out_tests.append({**{k: item[k] for k in ("status", "run_id", "symbol") if k in item}, **cmp,
                          "raw": item["raw"]})
    issues += check_account(st, report.get("account_settings"))
    brackets = [x["stop_out"]["bracket_model"] for x in out_tests if x.get("purpose") == "stop_out"
                and x.get("match") and x["stop_out"]["bracket_model"]]
    implied = [max(b[0] for b in brackets), min(b[1] for b in brackets)] if brackets else None
    so_pct = float(st.cfg["account"]["stop_out_pct"])
    if not implied or not (implied[0] <= so_pct < implied[1]):
        issues.append(f"#14 the stop-out tests do not bracket {so_pct}%: {implied}")
    match = not issues and all(x.get("match") for x in out_tests)
    return {"definition": DEFINITION, "config_hash": st.cfg_hash, "model_version": t1.T1_VERSION,
            "tests": out_tests, "account_settings": report.get("account_settings"),
            "implied_stop_out_bracket": implied, "account_issues": issues, "n_tests": len(want),
            "match": match}


def save(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
