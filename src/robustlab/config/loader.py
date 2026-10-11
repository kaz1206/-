"""YAML loading and validation (P1_PLAN §4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from robustlab.core.ids import canonical_json, sha256_hex
from robustlab.core.models import (
    BacktestRequest,
    DataPartition,
    ProfileFile,
    RiskProfile,
    RiskProfileFile,
    CostStressFile,
    SelectionProfile,
    SelectionProfileFile,
    ValidationPlanSpec,
    StrategyProfile,
    StrategySpec,
    StudyRequest,
    TerminalConfig,
    find_credential_keys,
)


class ConfigError(ValueError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"file not found: {path}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    hits = find_credential_keys(data)
    if hits:
        raise ConfigError(f"{path}: credential-like keys are not allowed: {hits}")
    return data


def _parse(model: type, path: Path, data: dict[str, Any]):
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_terminal_config(path: Path) -> TerminalConfig:
    return _parse(TerminalConfig, path, load_yaml(path))


def load_partition(path: Path) -> tuple[DataPartition, str]:
    """Returns the partition and its hash (the hash pins the holdout definition, P1_PLAN §2.4-2)."""
    data = load_yaml(path)
    partition = _parse(DataPartition, path, data)
    dump = partition.model_dump(mode="json")
    if not partition.rehearsal:
        dump.pop("rehearsal")  # keeps the hash of every partition pinned before P8b unchanged
    return partition, sha256_hex(canonical_json(dump))


@dataclass(frozen=True)
class LoadedRequest:
    request: BacktestRequest
    request_path: Path
    strategy: StrategySpec
    strategy_path: Path
    request_hash: str


def load_request(path: Path) -> LoadedRequest:
    data = load_yaml(path)
    request = _parse(BacktestRequest, path, data)
    strategy_path = (path.parent / request.strategy_file).resolve()
    strategy = _parse(StrategySpec, strategy_path, load_yaml(strategy_path))
    request_hash = sha256_hex(canonical_json(request.model_dump(mode="json")))
    return LoadedRequest(request, path.resolve(), strategy, strategy_path, request_hash)


@dataclass(frozen=True)
class LoadedStudy:
    study: StudyRequest
    study_path: Path
    strategy: StrategySpec
    strategy_path: Path
    study_hash: str


def load_study(path: Path) -> LoadedStudy:
    data = load_yaml(path)
    study = _parse(StudyRequest, path, data)
    strategy_path = (path.parent / study.strategy_file).resolve()
    strategy = _parse(StrategySpec, strategy_path, load_yaml(strategy_path))
    study_hash = sha256_hex(canonical_json(study.model_dump(mode="json")))
    return LoadedStudy(study, path.resolve(), strategy, strategy_path, study_hash)


def load_selection_profile(path: Path, name: str) -> tuple[SelectionProfile, str]:
    """One selection profile and the hash of its content (recorded with every P7 decision)."""
    pf = _parse(SelectionProfileFile, path, load_yaml(path))
    if name not in pf.profiles:
        raise ConfigError(f"{path}: unknown selection profile {name!r} (have {sorted(pf.profiles)})")
    prof = pf.profiles[name]
    return prof, sha256_hex(canonical_json({"name": name, **prof.model_dump(mode="json")}))


def load_risk_profile(path: Path, name: str) -> tuple[RiskProfile, str]:
    """One P9 risk profile and the hash of its content (recorded with every P9 execution)."""
    pf = _parse(RiskProfileFile, path, load_yaml(path))
    if name not in pf.profiles:
        raise ConfigError(f"{path}: unknown risk profile {name!r} (have {sorted(pf.profiles)})")
    prof = pf.profiles[name]
    return prof, sha256_hex(canonical_json({"name": name, **prof.model_dump(mode="json")}))


def load_validation_plan(path: Path) -> tuple[ValidationPlanSpec, Path, str]:
    """(plan, resolved strategy path, hash of the plan content) - P8_PLAN §3."""
    plan = _parse(ValidationPlanSpec, path, load_yaml(path))
    strategy_path = (path.parent / plan.strategy_file).resolve()
    return plan, strategy_path, sha256_hex(canonical_json(plan.model_dump(mode="json", by_alias=True)))


def load_cost_stress(path: Path) -> tuple[CostStressFile, str]:
    cs = _parse(CostStressFile, path, load_yaml(path))
    return cs, sha256_hex(canonical_json(cs.model_dump(mode="json")))


def load_profile(path: Path, name: str) -> tuple[StrategyProfile, str]:
    """One strategy profile and the hash of its content (recorded with every decision, P4)."""
    pf = _parse(ProfileFile, path, load_yaml(path))
    if name not in pf.profiles:
        raise ConfigError(f"{path}: unknown strategy profile {name!r} (have {sorted(pf.profiles)})")
    prof = pf.profiles[name]
    return prof, sha256_hex(canonical_json({"name": name, **prof.model_dump(mode="json")}))
