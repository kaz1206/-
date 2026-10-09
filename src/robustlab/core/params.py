"""Parameter normalization (ARCHITECTURE §8.1, P1_PLAN §2.3).

Normalized values are what goes into candidate_id and into the .set file:
int/enum -> int, bool -> bool, double -> 15-significant-digit string,
string -> str, datetime -> int seconds (MT5 datetime, broker server time, no TZ).
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
from typing import Any

from robustlab.core.models import InputSpec, InputType


class ParamError(ValueError):
    pass


def _as_int(name: str, v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ParamError(f"{name}: expected an integer, got {v!r}")
    return v


def _datetime_seconds(name: str, v: Any) -> int:
    if isinstance(v, bool):
        raise ParamError(f"{name}: expected a datetime, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError as e:
            raise ParamError(f"{name}: invalid datetime {v!r}") from e
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            raise ParamError(f"{name}: datetimes are broker server time and must not carry a timezone")
        return calendar.timegm(v.timetuple())
    if isinstance(v, dt.date):
        return calendar.timegm(v.timetuple())
    raise ParamError(f"{name}: expected a datetime, got {v!r}")


def normalize_value(spec: InputSpec, v: Any) -> int | bool | str:
    name = spec.name
    match spec.type:
        case InputType.INT | InputType.ENUM:
            return _as_int(name, v)
        case InputType.BOOL:
            if not isinstance(v, bool):
                raise ParamError(f"{name}: expected true/false, got {v!r}")
            return v
        case InputType.DOUBLE:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ParamError(f"{name}: expected a finite number, got {v!r}")
            return format(float(v), ".15g")
        case InputType.STRING:
            if not isinstance(v, str):
                raise ParamError(f"{name}: expected a string, got {v!r}")
            if any(c in v for c in "\r\n\x00"):
                raise ParamError(f"{name}: strings must not contain line breaks or NUL")
            return v
        case InputType.DATETIME:
            return _datetime_seconds(name, v)
    raise ParamError(f"{name}: unsupported type {spec.type}")  # pragma: no cover


def normalize_params(params: dict[str, Any], inputs: list[InputSpec]) -> dict[str, int | bool | str]:
    """Validate that params match the declared inputs exactly, and normalize them."""
    declared = {i.name: i for i in inputs}
    missing = sorted(set(declared) - set(params))
    extra = sorted(set(params) - set(declared))
    if missing or extra:
        raise ParamError(f"params do not match the strategy inputs (missing={missing}, unknown={extra})")
    return {name: normalize_value(declared[name], params[name]) for name in sorted(params)}
