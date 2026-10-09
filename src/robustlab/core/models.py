"""Typed configuration and request models (P1_PLAN §4, §2.3, §15)."""

from __future__ import annotations

import datetime as dt
import enum
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from robustlab.core.ids import HARNESS_INPUT_PREFIX

# Keys that would carry credentials. They are rejected anywhere in P1 configs (P1_PLAN §2.4, §13-7).
CREDENTIAL_KEYS = frozenset({"login", "password", "pass", "investor", "server", "certpassword"})


class TickModel(str, enum.Enum):
    """Only the values confirmed on hardware are allowed (W4, [S-HW-P1])."""

    REAL_TICKS = "REAL_TICKS"
    OHLC_M1 = "OHLC_M1"


MODEL_CODES: dict[TickModel, int] = {TickModel.REAL_TICKS: 4, TickModel.OHLC_M1: 1}

# MT5 ENUM_TIMEFRAMES values, taken from the official MetaTrader5 package constants [S-MT5PY].
TIMEFRAME_CODES: dict[str, int] = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12,
    "M15": 15, "M20": 20, "M30": 30,
    "H1": 1 | 0x4000, "H2": 2 | 0x4000, "H3": 3 | 0x4000, "H4": 4 | 0x4000,
    "H6": 6 | 0x4000, "H8": 8 | 0x4000, "H12": 12 | 0x4000, "D1": 24 | 0x4000,
    "W1": 1 | 0x8000, "MN1": 1 | 0xC000,
}


class JobStatus(str, enum.Enum):
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_TO_START = "FAILED_TO_START"
    TIMED_OUT = "TIMED_OUT"
    TELEMETRY_MISSING = "TELEMETRY_MISSING"
    QUARANTINED = "QUARANTINED"
    ABANDONED = "ABANDONED"
    INTERNAL_ERROR = "INTERNAL_ERROR"  # unexpected exception in rlab after the job was created


class InputType(str, enum.Enum):
    INT = "int"
    DOUBLE = "double"
    BOOL = "bool"
    STRING = "string"
    ENUM = "enum"
    DATETIME = "datetime"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputSpec(_Strict):
    name: str
    type: InputType

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v or not v.replace("_", "").isalnum():
            raise ValueError(f"invalid input name {v!r}")
        if v.startswith(HARNESS_INPUT_PREFIX):
            raise ValueError(f"{v!r}: names starting with {HARNESS_INPUT_PREFIX} are reserved for the telemetry harness")
        return v


class StrategySpec(_Strict):
    strategy_name: str
    expert: str = Field(description=r"Path under MQL5\Experts without extension, e.g. RobustLab\RL_SmokeTest")
    mq5_path: str | None = Field(default=None, description="Source file, relative to the strategy YAML (optional)")
    telemetry_version: str
    inputs: list[InputSpec]

    @field_validator("expert")
    @classmethod
    def _expert(cls, v: str) -> str:
        if v.lower().endswith(".ex5") or ".." in v or v.startswith(("\\", "/")) or ":" in v:
            raise ValueError("expert must be a relative path under MQL5\\Experts without the .ex5 extension")
        if not v.isascii():
            raise ValueError("expert path must be ASCII (the ini file is written in ASCII, W1)")
        return v

    @model_validator(mode="after")
    def _unique(self) -> StrategySpec:
        names = [i.name for i in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError("duplicate input names")
        return self


class TesterSettings(_Strict):
    model: TickModel
    execution_delay_ms: int = 0
    deposit: float
    currency: str
    leverage: int

    @field_validator("execution_delay_ms")
    @classmethod
    def _delay(cls, v: int) -> int:
        if v != 0:
            raise ValueError("P1 supports execution_delay_ms = 0 only (ExecutionMode values are unverified)")
        return v

    @field_validator("deposit")
    @classmethod
    def _deposit(cls, v: float) -> float:
        if not (v > 0 and math.isfinite(v)):
            raise ValueError("deposit must be positive")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        if len(v) != 3 or not v.isalpha() or not v.isupper():
            raise ValueError("currency must be a 3-letter uppercase code")
        return v

    @field_validator("leverage")
    @classmethod
    def _leverage(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("leverage must be positive")
        return v


class BacktestRequest(_Strict):
    strategy_file: str = Field(description="Strategy YAML path, relative to the request file")
    symbol: str
    timeframe: str
    from_date: dt.date
    to_date: dt.date = Field(description="Exclusive end (MT5 ToDate is not included, W12)")
    params: dict[str, Any]
    tester: TesterSettings
    coverage_tolerance_days: int = 7

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        if not v or not v.isascii() or any(c.isspace() for c in v):
            raise ValueError("symbol must be a non-empty ASCII string without spaces")
        return v

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, v: str) -> str:
        if v not in TIMEFRAME_CODES:
            raise ValueError(f"unknown timeframe {v!r}")
        return v

    @field_validator("coverage_tolerance_days")
    @classmethod
    def _tol(cls, v: int) -> int:
        if v < 0:
            raise ValueError("coverage_tolerance_days must be >= 0")
        return v

    @model_validator(mode="after")
    def _period(self) -> BacktestRequest:
        if self.from_date >= self.to_date:
            raise ValueError("from_date must be earlier than to_date")
        return self


class TerminalConfig(_Strict):
    terminal_path: Path = Field(description="Portable terminal64.exe")
    data_dir: Path | None = Field(default=None, description="Data folder; defaults to the terminal folder (portable)")
    common_files_dir: Path = Field(description=r"%APPDATA%\MetaQuotes\Terminal\Common\Files (W7)")
    start_timeout_sec: float = 300  # F7
    run_timeout_sec: float = 3600  # F7
    poll_interval_sec: float = 1.0

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir is not None else self.terminal_path.parent

    @model_validator(mode="after")
    def _timeouts(self) -> TerminalConfig:
        if self.start_timeout_sec <= 0 or self.run_timeout_sec <= 0 or self.poll_interval_sec <= 0:
            raise ValueError("timeouts must be positive")
        if self.start_timeout_sec > self.run_timeout_sec:
            raise ValueError("start_timeout_sec must not exceed run_timeout_sec")
        return self


class DataPartition(_Strict):
    """Holdout definition in broker server dates (P1_PLAN §2.4)."""

    holdout_from: dt.date
    holdout_to: dt.date
    embargo_days: int
    note: str = ""

    @model_validator(mode="after")
    def _valid(self) -> DataPartition:
        if self.holdout_from > self.holdout_to:
            raise ValueError("holdout_from must not be after holdout_to")
        if self.embargo_days < 0:
            raise ValueError("embargo_days must be >= 0")
        return self


def find_credential_keys(obj: Any, path: str = "") -> list[str]:
    """Return dotted paths of any credential-like keys, at any depth."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k).strip().lower() in CREDENTIAL_KEYS:
                hits.append(p)
            hits.extend(find_credential_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_credential_keys(v, f"{path}[{i}]"))
    return hits
