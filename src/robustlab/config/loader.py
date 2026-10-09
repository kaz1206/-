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
    StrategySpec,
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
    return partition, sha256_hex(canonical_json(partition.model_dump(mode="json")))


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
