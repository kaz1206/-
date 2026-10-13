"""Study A fidelity plumbing on the fake terminal (SPEC §3, §12): export -> 12 regular + 3 stop-out single tests on the
custom symbols -> comparison t2 (raw tester data kept) -> report; fidelity-check re-judges the saved raw data. The
fake replays the T1 model's own deals and daily rows, so everything must match; a shifted price, a daily equity off by
2 cents, a missing stop-out, a different stop-out level and a missing account-settings file must each be caught.
(The real check runs on MT5; this only proves the machinery around it.)"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.studya import evaluate as E
from robustlab.studya import fidelity as fd

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs" / "study_a.yaml"

ACCOUNT = {"script": "RL_ImportT1", "version": "1.01",
           "account": {"currency": "USD", "so_mode": "ACCOUNT_STOPOUT_MODE_PERCENT", "so_so": 30.0, "so_call": 50.0,
                       "trade_mode": "ACCOUNT_TRADE_MODE_DEMO", "margin_mode": "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING"},
           "symbols": {s: {"calc_mode": "SYMBOL_CALC_MODE_FOREX", "margin_initial": 0.0, "margin_rate_buy_initial": 1.0,
                           "margin_rate_sell_initial": 1.0, "contract_size": 100000.0, "currency_margin": "EUR",
                           "currency_profit": "USD"} for s in fd.SYMBOLS.values()}}


def _entry(r) -> dict:
    return {"stats": r.stats, "tracking": r.tracking, "daily": [dict(x) for x in r.daily],
            "deals": [dict(d) for d in r.deals]}


def _doc(st, mutate=None) -> dict:
    days, w = fd.layout(st)
    start = days[w.s_start // st.spd].isoformat()
    doc = {"symbols": {fd.SYMBOLS[k]: {"axes": ["InpLookback", "InpHoldBars"], "cells": {},
                                       "env_symbol": {"spread_float": False, "swap_long": 0.0, "swap_short": 0.0,
                                                      "observed_spread": 10, "name": fd.SYMBOLS[k]}}
                       for k in ("H0", "E1")}}
    for t in fd.tests(st):
        e = _entry(fd.model(st, t))
        if mutate:
            mutate(t, e)
        cell = doc["symbols"][fd.SYMBOLS[t.kind]]["cells"].setdefault(f"{t.lookback}_{t.hold}", {}).setdefault(start, {})
        if t.purpose == "regular":
            cell.update(e)
        else:
            cell.setdefault("by_deposit", {})[f"{t.deposit:.0f}"] = e
    return doc


def _run(env, monkeypatch, tmp_path, doc, account=ACCOUNT) -> tuple[int, dict, str]:
    (env.data / "MQL5" / "Experts" / "RobustLab" / "RL_TSMom.ex5").write_bytes(b"tsmom-ex5")
    tmp_path.mkdir(parents=True, exist_ok=True)
    acct = env.common / fd.ACCOUNT_FILE
    if account is None:
        acct.unlink(missing_ok=True)
    else:
        acct.write_text(json.dumps(account))
    series = tmp_path / "fid_series.json"
    series.write_text(json.dumps(doc))
    monkeypatch.setenv("FAKE_SERIES_FILE", str(series))
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setattr("robustlab.studya.run.RESULTS", tmp_path / "results")
    res = CliRunner().invoke(app, ["studya", "fidelity-run", "--terminal", str(env.terminal_cfg), "--partition",
                                   str(env.partition), "--workspace", str(tmp_path / "ws_fid")])
    rep = json.loads((tmp_path / "results" / "fidelity_t1.json").read_text())
    return res.exit_code, rep, res.output


def _bad(rep) -> list[dict]:
    return [t for t in rep["tests"] if not t["match"]]


def test_fidelity_plumbing_matches_and_catches_mismatches(env, monkeypatch, tmp_path):
    st = E.setup(CFG)
    out = fd.export(st, tmp_path / "common")
    assert out["H0"]["rows"] == out["E1"]["rows"] > 0
    code, rep, txt = _run(env, monkeypatch, tmp_path / "ok", _doc(st))
    assert code == 0, txt + json.dumps(rep, indent=1)[:3000]
    assert rep["match"] and rep["definition"] == "t2" and len(rep["tests"]) == 15
    so = [t for t in rep["tests"] if t["purpose"] == "stop_out"]
    assert len(so) == 3 and all(t["stop_out"]["model"] == t["stop_out"]["tester"] == 1 for t in so)
    lo, hi = rep["implied_stop_out_bracket"]
    assert lo <= 30.0 < hi < 50.0
    assert all(t["daily"]["days_model"] == t["daily"]["days_tester"] == 520 for t in rep["tests"])
    assert all("account_server" not in t["raw"]["env"] for t in rep["tests"])
    # fidelity-check re-judges the saved raw data without the terminal
    res = CliRunner().invoke(app, ["studya", "fidelity-check"])
    assert res.exit_code == 0, res.output

    # 1. a shifted entry price
    def shift(t, e):
        if t.name == "E1_126_6":
            d = next(d for d in e["deals"] if d["type"] in ("0", "1"))
            d["price"] = f"{float(d['price']) + 0.0002:.5f}"
    code, rep, _ = _run(env, monkeypatch, tmp_path / "shift", _doc(st, shift))
    assert code == 2 and [t["name"] for t in _bad(rep)] == ["E1_126_6"]
    assert any(i.startswith(("#2", "#8")) for i in _bad(rep)[0]["issues"])

    # 2. daily equity off by 2 cents on one day
    def cents(t, e):
        if t.name == "H0_756_30":
            e["daily"][100]["equity_close"] = f"{float(e['daily'][100]['equity_close']) + 0.02:.2f}"
    code, rep, _ = _run(env, monkeypatch, tmp_path / "daily", _doc(st, cents))
    assert code == 2 and [t["name"] for t in _bad(rep)] == ["H0_756_30"]
    assert any(i.startswith("#9") for i in _bad(rep)[0]["issues"])

    # 3. the tester stops out one tick later with another reason code (= another level / no stop-out rule)
    def late(t, e):
        if t.name == "SO_H0_126_30_200":
            d = next(d for d in e["deals"] if d["reason"] == "6")
            d["reason"] = "3"
            e["tracking"] = {**e["tracking"], "stop_out_deals": 0}
    code, rep, _ = _run(env, monkeypatch, tmp_path / "late", _doc(st, late))
    assert code == 2 and [t["name"] for t in _bad(rep)] == ["SO_H0_126_30_200"]
    assert any(i.startswith("#10") for i in _bad(rep)[0]["issues"])

    # 4. a deal after the stop-out (orders not refused)
    def after(t, e):
        if t.name == "SO_E1_1512_6_300":
            so = next(d for d in e["deals"] if d["reason"] == "6")
            extra = dict(so, ticket="99", time="2011.06.01 00:00:00", entry="0", reason="3", profit="0.00")
            e["deals"].append(extra)
            e["stats"] = {**e["stats"], "deals": e["stats"]["deals"] + 1}
    code, rep, _ = _run(env, monkeypatch, tmp_path / "after", _doc(st, after))
    assert code == 2 and "SO_E1_1512_6_300" in [t["name"] for t in _bad(rep)]
    so_issues = next(t for t in _bad(rep) if t["name"] == "SO_E1_1512_6_300")["issues"]
    assert any(i.startswith("#11") for i in so_issues), so_issues

    # 5. a different margin calculation (min margin level off)
    def margin(t, e):
        if t.name == "SO_H0_1512_6_300":
            e["tracking"] = {**e["tracking"], "min_margin_level": e["tracking"]["min_margin_level"] + 0.5}
    code, rep, _ = _run(env, monkeypatch, tmp_path / "margin", _doc(st, margin))
    assert code == 2 and any(i.startswith("#12") for i in _bad(rep)[0]["issues"])

    # 6. account settings: missing file / another stop-out level -> not confirmed
    code, rep, _ = _run(env, monkeypatch, tmp_path / "noacct", _doc(st), account=None)
    assert code == 2 and not _bad(rep) and any("missing" in i for i in rep["account_issues"])
    other = json.loads(json.dumps(ACCOUNT))
    other["account"]["so_so"] = 50.0
    code, rep, _ = _run(env, monkeypatch, tmp_path / "so50", _doc(st), account=other)
    assert code == 2 and any(i.startswith("#14 stop-out setting") for i in rep["account_issues"])
