"""P8b prediction band (fix1) and partition compatibility (P8B_PLAN §2.1, §2.4, §6.2)."""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from robustlab.config.loader import load_partition
from robustlab.oos.manifest import weekdays
from robustlab.stats import validation as ev

ROOT = Path(__file__).resolve().parents[2]


def check_module():
    spec = importlib.util.spec_from_file_location("obc", ROOT / "docs/research/p8_simulations/oos_band_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_band_equals_the_independent_check_fix1():
    obc = check_module()
    rng = np.random.default_rng(3)
    for _ in range(40):
        s0, s1 = rng.normal(0.0004, 0.01, 400), np.convolve(rng.standard_normal(122), [1, 0.4, 0.2], "valid") * 0.01
        e = ev.evidence([s0, s1], 200, np.random.default_rng(int(rng.integers(1 << 30))))
        assert ev.oos_band(e, 520)["lower"] == obc.band(e, 520)["lower1"]


def test_band_by_hand():
    # SR 0.05, skew 0, kurtosis 3, n 631, n_AR1 631, n_NW 600, bootstrap SE 0.045, n_oos 520
    e = {"sharpe": 0.05, "skewness": 0.0, "kurtosis": 3.0, "n": 631, "n_ar1": 631.0, "n_newey_west": 600.0,
         "bootstrap_se": 0.045, "reason": None}
    v = 1 + 0.5 * 0.05 ** 2                       # 1.00125
    se_an = math.sqrt(v / 599)                    # n_eff = 600
    se_val = max(se_an, 0.045)                    # 0.045: the bootstrap SE is larger
    n_star = v / se_val ** 2 + 1
    se_oos = math.sqrt(v / (520 * n_star / 631 - 1))
    lower = 0.05 - 1.6448536269514722 * math.sqrt(se_val ** 2 + se_oos ** 2)
    b = ev.oos_band(e, 520)
    assert b["se_analytic"] == pytest.approx(se_an) and b["se_val"] == 0.045
    assert b["se_oos"] == pytest.approx(se_oos) and b["lower"] == pytest.approx(lower)
    # the OOS SE carries the same correction as the validation SE (fix1): same n_eff* / n ratio
    assert (b["n_eff_star"] / 631) == pytest.approx((520 * n_star / 631) / 520)


def test_band_refuses_missing_evidence():
    assert ev.oos_band({"reason": "no evidence", "sharpe": None}, 520)["lower"] is None


def test_existing_partition_hash_is_unchanged_by_the_rehearsal_field():
    # pinned in every research workspace since P1; adding `rehearsal` must not change it
    _, h = load_partition(ROOT / "configs" / "data_partition.yaml")
    assert h == "cba8cbe59950c279c2aa25a8ede8175bef05fec10dd775da9c51d5b2d165a311"
    p, h2 = load_partition(ROOT / "configs" / "rehearsal" / "data_partition.yaml")
    assert p.rehearsal is True and h2 != h


def test_weekdays():
    import datetime as dt
    assert weekdays(dt.date(2024, 7, 1), dt.date(2024, 7, 8)) == 5
