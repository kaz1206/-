"""Study A fidelity plumbing on the fake terminal (SPEC §3): export -> 12 single tests on the custom symbols ->
comparison t1 -> report. The fake replays the T1 model's own deals, so everything must match; a shifted price must
be caught. (The real check runs on MT5; this only proves the machinery around it.)"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from robustlab.cli.main import app
from robustlab.studya import evaluate as E
from robustlab.studya import fidelity as fd

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "configs" / "study_a.yaml"


def _doc(st, shift_first_price: bool = False) -> dict:
    days, w = fd.layout(st)
    start = days[w.s_start // st.spd].isoformat()
    doc = {"symbols": {}}
    for kind in ("H0", "E1"):
        cells = {}
        for L, H in st.cfg["fidelity"]["cells"]:
            r = fd.model(st, kind, int(L), int(H))
            deals = [dict(d) for d in r.deals]
            if shift_first_price and kind == "E1" and (L, H) == (126, 6):
                first = next(d for d in deals if d["type"] in ("0", "1"))
                first["price"] = f"{float(first['price']) + 0.0002:.5f}"
            cells[f"{L}_{H}"] = {start: {"stats": r.stats, "tracking": r.tracking, "daily": r.daily, "deals": deals}}
        doc["symbols"][fd.SYMBOLS[kind]] = {"axes": ["InpLookback", "InpHoldBars"], "cells": cells,
                                            "env_symbol": {"spread_float": False, "swap_long": 0.0, "swap_short": 0.0,
                                                           "observed_spread": 10, "name": fd.SYMBOLS[kind]}}
    return doc


def _run(env, monkeypatch, tmp_path, doc) -> tuple[int, dict]:
    (env.data / "MQL5" / "Experts" / "RobustLab" / "RL_TSMom.ex5").write_bytes(b"tsmom-ex5")
    tmp_path.mkdir(parents=True, exist_ok=True)
    series = tmp_path / "fid_series.json"
    series.write_text(json.dumps(doc))
    monkeypatch.setenv("FAKE_SERIES_FILE", str(series))
    monkeypatch.setenv("FAKE_MODE", "ok")
    monkeypatch.setattr("robustlab.studya.run.RESULTS", tmp_path / "results")
    res = CliRunner().invoke(app, ["studya", "fidelity-run", "--terminal", str(env.terminal_cfg), "--partition",
                                   str(env.partition), "--workspace", str(tmp_path / "ws_fid")])
    rep = json.loads((tmp_path / "results" / "fidelity_t1.json").read_text())
    return res.exit_code, rep


def test_fidelity_plumbing_matches_and_catches_a_shift(env, monkeypatch, tmp_path):
    st = E.setup(CFG)
    out = fd.export(st, tmp_path / "common")
    assert out["H0"]["rows"] == out["E1"]["rows"] > 0
    code, rep = _run(env, monkeypatch, tmp_path, _doc(st))
    assert code == 0, json.dumps(rep, indent=1)[:3000]
    assert rep["match"] and len(rep["tests"]) == 12
    assert all(t["end_of_test_deals"][0] == t["end_of_test_deals"][1] for t in rep["tests"])
    code, rep = _run(env, monkeypatch, tmp_path / "bad", _doc(st, shift_first_price=True))
    assert code == 2 and not rep["match"]
    bad = [t for t in rep["tests"] if not t["match"]]
    assert len(bad) == 1 and bad[0]["kind"] == "E1" and any(i.startswith("#2") or i.startswith("#8")
                                                              for i in bad[0]["issues"])
