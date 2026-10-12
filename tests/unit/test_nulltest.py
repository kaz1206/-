"""P10 simulator, H0 price model and execution rules (P10_PLAN §1.3-§1.4, §6)."""

import json
import textwrap
from pathlib import Path

import numpy as np
import pytest
import yaml

from robustlab.nulltest import pipeline as pl
from robustlab.nulltest import run as nr
from robustlab.nulltest import sim
from robustlab.storage.db import Database, now_iso

ROOT = Path(__file__).resolve().parents[2]
ACC = sim.Account(deposit=10000.0, leverage=100.0, contract=100000.0, pip=0.0001, spread=0.0001, stop_out_pct=50.0,
                  start_price=1.10)
PM = sim.PriceModel(sigma_bar=0.0022, garch_alpha=0.05, garch_beta=0.93, t_df=5, bars_per_day=6, steps_per_bar=8)


def test_hand_cases():
    """The four cases of P10_PLAN §5 (worked out by hand before the run-2 simulation)."""
    bk = sim.run_trend(np.array([[1.10, 1.11, 1.12, 1.11]]), np.array([1]), np.array([2]), 0.10, ACC, 6, 1)
    assert round(bk.bal[0], 2) == 9898.00 and bk.trades[0] == 1
    path = np.array([[1.1000, 1.1012, 1.0997, 1.0999]])
    bk = sim.run_grid(path, np.array([10.0]), np.array([10.0]), np.array([2.0]), 0.01, 12, ACC, 6, 8)
    assert round(bk.bal[0], 2) == 10003.00 and bk.trades[0] == 2 and round(bk.cost[0], 2) == 0.30
    path = np.array([[1.1000, 1.1012, 1.0997, 1.0995]])
    bk = sim.run_grid(path, np.array([10.0]), np.array([10.0]), np.array([1.0]), 0.05, 10, ACC, 6, 8)
    assert round(bk.bal[0], 2) == 10010.00 and bk.trades[0] == 2 and round(bk.cost[0], 2) == 1.00
    bk = sim.run_grid(np.array([[1.1000, 1.1900, 1.2000]]), np.array([1000.0]), np.array([10.0]), np.array([2.0]),
                      1.0, 12, ACC, 6, 8)
    assert bk.stop_outs[0] == 1 and round(bk.bal[0], 2) == -10.00


def test_martingale_level_lots_and_max_levels():
    # with no history the first entry is SHORT; a steady rise of 1 pip per step adds every 10 pips with lots
    # 0.01 * 2^k, capped at 3 levels (0.01 + 0.02 + 0.04); no take-profit, no new basket
    path = np.array([[1.1000 + 0.0001 * k for k in range(49)]])
    bk = sim.run_grid(path, np.array([10.0]), np.array([10.0]), np.array([2.0]), 0.01, 3, ACC, 6, 8)
    assert bk.maxpos[0] == 3
    assert round(bk.cost[0], 4) == round(0.0001 * 100000 * (0.01 + 0.02 + 0.04), 4)


def test_h0_price_is_a_martingale():
    """C1: the next step's mean is 0 and not predictable from past steps or their size (P10_PLAN §1.4)."""
    p = sim.prices(40, 120, np.random.default_rng(1), PM, 1.10)
    d = np.diff(p, axis=1)
    y, x1, x2 = d[:, 2:].ravel(), d[:, 1:-1].ravel(), np.abs(d[:, 1:-1]).ravel()
    X = np.column_stack([np.ones_like(x1), x1, x2])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * resid.var())
    assert np.all(np.abs(coef / se) < 4)
    # the volatility does cluster at the bar level (GARCH per H4 bar, kept on purpose)
    bars = np.abs(np.diff(p[:, ::8], axis=1))
    assert np.corrcoef(bars[:, 1:].ravel(), bars[:, :-1].ravel())[0, 1] > 0.05


def test_power_paths_have_the_requested_autocorrelation():
    p = sim.prices(40, 200, np.random.default_rng(2), PM, 1.10, ar=-0.3)
    bars = np.diff(p[:, ::8], axis=1)
    rho = np.corrcoef(bars[:, 1:].ravel(), bars[:, :-1].ravel())[0, 1]
    assert -0.36 < rho < -0.24


def test_s1_is_close_to_zero_for_each_archetype():
    cfg = yaml.safe_load((ROOT / "configs" / "null_archetypes.yaml").read_text())
    for name, spec in cfg["archetypes"].items():
        path = sim.prices(30, 60, np.random.default_rng([3, len(name)]), PM, 1.10)
        bk = sim.simulate(spec, path, ACC, PM)
        gross = bk.eq_close[-1] - ACC.deposit + bk.cost
        per_round = gross.reshape(30, -1).mean(axis=1)
        z = per_round.mean() / (per_round.std(ddof=1) / np.sqrt(30))
        assert abs(z) < 4, (name, z)


def test_verdict():
    crit = {"max_upper_bound": 0.05, "s1_max_se": 3.0}
    ok = {"s1": {"z": 1.0}, "ci": pl.clopper_pearson(38, 1000)}
    assert nr.verdict(ok, crit, "judgement") == "MET"
    assert nr.verdict({"s1": {"z": 1.0}, "ci": pl.clopper_pearson(39, 1000)}, crit, "judgement") == "NOT_MET"
    assert nr.verdict({"s1": {"z": 3.5}, "ci": pl.clopper_pearson(0, 1000)}, crit, "judgement") == "INVALID_S1"
    assert nr.verdict(ok, crit, "reproduction") == "RECORDED"


def small_config(tmp_path):
    cfg = yaml.safe_load((ROOT / "configs" / "null_archetypes.yaml").read_text())
    cfg["days"] = {"D": 70, "V0": 40, "V1": 30, "F": 20}
    cfg["chunk"] = 2
    cfg["modes"]["reproduction"]["rounds"] = 3
    p = tmp_path / "null.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


def test_execution_is_recorded_and_judgement_runs_once(tmp_path):
    cfg = small_config(tmp_path)
    prof = ROOT / "configs" / "strategy_profiles.yaml"
    out = nr.run(tmp_path, "A2", "reproduction", cfg, prof)
    assert out.status == "DONE" and out.summary["verdict"] in ("RECORDED", "INVALID_S1")
    assert out.summary["result"]["rounds"] == 3 and len(out.summary["notes"]) == 4
    assert nr.run(tmp_path, "A2", "reproduction", cfg, prof).status == "ALREADY_DONE"
    # a judgement already on record for this configuration is never run again
    c, h = nr.load_config(cfg)
    db = Database(tmp_path / "robustlab.sqlite")
    try:
        db.insert("null_execution", {"null_id": "nl_prior", "archetype": "A1", "mode": "judgement", "kind": "null",
                                     "rounds": 1000, "seed": 20261013, "evidence_seeding": "global",
                                     "config_json": json.dumps(c), "config_hash": h, "profiles_hash": "x",
                                     "component_name": "nulltest", "component_version": "old", "verdict": "MET",
                                     "criterion_json": json.dumps(c["criterion"]),
                                     "result_json": json.dumps(out.summary["result"]), "created_at": now_iso()})
        with pytest.raises(Exception):
            db.conn.execute("UPDATE null_execution SET verdict = 'NOT_MET'")
    finally:
        db.close()
    again = nr.run(tmp_path, "A1", "judgement", cfg, prof)
    assert again.status == "ALREADY_JUDGED" and again.null_id == "nl_prior"


def test_config_guards(tmp_path):
    cfg = yaml.safe_load((ROOT / "configs" / "null_archetypes.yaml").read_text())
    cfg["modes"]["judgement"]["rounds"] = 100
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(nr.NullError):
        nr.load_config(p)
    assert nr.run(tmp_path, "A9", "reproduction", ROOT / "configs" / "null_archetypes.yaml",
                  ROOT / "configs" / "strategy_profiles.yaml").status == "INPUT_INVALID"
    assert nr.run(tmp_path, "A1", "whatever", ROOT / "configs" / "null_archetypes.yaml",
                  ROOT / "configs" / "strategy_profiles.yaml").status == "INPUT_INVALID"


def test_fidelity_export_and_model(tmp_path):
    from robustlab.nulltest import fidelity as fd

    import datetime as dt

    info = fd.export(tmp_path / "h0.csv", 3, PM, 1.10)
    lines = (tmp_path / "h0.csv").read_text().splitlines()
    assert info["rows"] == len(lines) == (fd.PRE_DAYS + 3) * 48 * 30
    first = lines[0].split(",")
    start = dt.datetime.combine(fd.data_start(), dt.time()).replace(tzinfo=dt.UTC)
    assert int(first[0]) == int(start.timestamp()) and first[6] == "10"
    assert fd.data_start().weekday() < 5 and info["test_from"] == "2012-01-02" and info["to_exclusive"] == "2012-01-05"
    # the model on a hand-made path: closes rise then fall -> one buy, then close + sell
    n_days = 40
    path = fd.path_of(n_days, PM, 1.10)
    deals = fd.smoketest_model(path, n_days, 2, 5, PM)
    assert deals and deals[0].entry == "in" and deals[0].time >= fd.START
    assert all(a.time <= b.time for a, b in zip(deals, deals[1:], strict=False))
    # a tester record identical to the model matches; a shifted one does not
    rows = [{"ticket": str(i), "time": d.time.strftime("%Y.%m.%d %H:%M:%S"), "type": "0" if d.side == "buy" else "1",
             "entry": "0" if d.entry == "in" else "1", "price": f"{d.price:.5f}"} for i, d in enumerate(deals)]
    assert fd.compare(deals, rows)["match"]
    rows[0]["price"] = f"{deals[0].price + 0.0001:.5f}"
    assert fd.compare(deals, rows)["mismatches"] == 1
