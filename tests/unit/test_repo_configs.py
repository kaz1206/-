"""The configs shipped in the repository load and behave as the E2E runbook expects."""

from pathlib import Path

from robustlab.config.loader import load_partition, load_request
from robustlab.core.params import normalize_params
from robustlab.oos.holdout_guard import check_overlap

ROOT = Path(__file__).resolve().parents[2]


def test_smoke_request_is_allowed_and_holdout_check_is_rejected():
    partition, _ = load_partition(ROOT / "configs" / "data_partition.yaml")
    smoke = load_request(ROOT / "configs" / "requests" / "smoketest_eurusd_h1.yaml")
    holdout = load_request(ROOT / "configs" / "requests" / "holdout_check_eurusd_h1.yaml")
    for lr in (smoke, holdout):
        normalize_params(lr.request.params, lr.strategy.inputs)
        assert lr.strategy.mq5_path and (lr.strategy_path.parent / lr.strategy.mq5_path).resolve().is_file()
    assert check_overlap(smoke.request.from_date, smoke.request.to_date, partition).approved
    assert not check_overlap(holdout.request.from_date, holdout.request.to_date, partition).approved


def test_mql5_sources_are_ascii():
    for p in (ROOT / "mql5").rglob("*.mq*"):
        assert p.read_bytes().isascii(), p
