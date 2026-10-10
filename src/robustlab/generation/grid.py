"""Full-grid expansion and chunking for MT5 optimization (P3_PLAN §2.1-2, §3.1, C43).

The grid is expanded exactly as MT5 does for `start||step||stop||Y`: every value
start + k*step up to stop, and every combination of the axes. Invalid combinations
(e.g. fast >= slow) are NOT removed: MT5 runs them, so they count as trials (C44).
Values are computed with Decimal so that 0.1-style steps do not drift.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from decimal import Decimal

from robustlab.core.ids import HARNESS_INPUT_PREFIX
from robustlab.core.models import InputSpec, InputType, StrategySpec, StudyRequest
from robustlab.core.params import ParamError, normalize_value

AXIS_TYPES = (InputType.INT, InputType.DOUBLE)
MATCH_REL_TOL = 1e-9


class GridError(ValueError):
    pass


def _dec(x: float) -> Decimal:
    return Decimal(format(x, ".15g"))


def _fmt(d: Decimal) -> str:
    return format(float(d), ".15g")


@dataclass(frozen=True)
class Axis:
    name: str
    type: InputType
    values: tuple[int | str, ...]  # normalized like core.params (int, or a .15g string for double)

    @property
    def start(self) -> str:
        return str(self.values[0])

    @property
    def stop(self) -> str:
        return str(self.values[-1])

    step: str = "1"

    def index_of(self, raw: str) -> int | None:
        """Index of an MT5-reported value (FrameInputs or XML text), or None."""
        try:
            x = float(raw)
        except ValueError:
            return None
        for i, v in enumerate(self.values):
            y = float(v)
            if x == y or math.isclose(x, y, rel_tol=MATCH_REL_TOL, abs_tol=MATCH_REL_TOL):
                return i
        return None

    def slice(self, lo: int, hi: int) -> Axis:
        return Axis(self.name, self.type, self.values[lo:hi], self.step)


@dataclass(frozen=True)
class Chunk:
    index: int
    axes: tuple[Axis, ...]

    @property
    def passes(self) -> int:
        return math.prod(len(a.values) for a in self.axes)

    def combos(self) -> list[tuple[int | str, ...]]:
        """Axis values per pass, in axis order."""
        return list(itertools.product(*(a.values for a in self.axes)))


@dataclass(frozen=True)
class Grid:
    axes: tuple[Axis, ...]  # in strategy input order
    fixed: dict[str, int | bool | str]  # normalized, every non-axis input
    chunks: tuple[Chunk, ...]

    @property
    def total(self) -> int:
        return math.prod(len(a.values) for a in self.axes)

    def params_for(self, axis_values: tuple[int | str, ...]) -> dict[str, int | bool | str]:
        p = dict(self.fixed)
        p.update({a.name: v for a, v in zip(self.axes, axis_values, strict=True)})
        return dict(sorted(p.items()))


def _axis(spec: InputSpec, start: float, step: float, stop: float) -> Axis:
    if spec.type not in AXIS_TYPES:
        raise GridError(f"{spec.name}: only int and double inputs can be optimized in P3 (got {spec.type.value})")
    s, d, e = _dec(start), _dec(step), _dec(stop)
    n = (e - s) / d
    if n != n.to_integral_value():
        raise GridError(f"{spec.name}: ({stop} - {start}) is not a multiple of step {step}")
    vals = [s + k * d for k in range(int(n) + 1)]
    if spec.type is InputType.INT:
        if any(v != v.to_integral_value() for v in (s, d)):
            raise GridError(f"{spec.name}: an int input needs integer start and step")
        return Axis(spec.name, spec.type, tuple(int(v) for v in vals), str(int(d)))
    return Axis(spec.name, spec.type, tuple(_fmt(v) for v in vals), _fmt(d))


def build_grid(study: StudyRequest, strategy: StrategySpec) -> Grid:
    declared = {i.name: i for i in strategy.inputs}
    unknown = sorted((set(study.param_space) | set(study.fixed)) - set(declared))
    if unknown:
        raise GridError(f"inputs not declared by the strategy: {unknown}")
    harness = sorted(n for n in study.fixed if n.startswith(HARNESS_INPUT_PREFIX))
    if harness:
        raise GridError(f"harness inputs cannot be set: {harness}")
    missing = sorted(set(declared) - set(study.param_space) - set(study.fixed))
    if missing:
        raise GridError(f"inputs neither optimized nor fixed: {missing}")

    axes = tuple(
        _axis(i, study.param_space[i.name].start, study.param_space[i.name].step, study.param_space[i.name].stop)
        for i in strategy.inputs if i.name in study.param_space
    )
    try:
        fixed = {n: normalize_value(declared[n], v) for n, v in sorted(study.fixed.items())}
    except ParamError as e:
        raise GridError(str(e)) from e

    first, rest = axes[0], axes[1:]
    per_value = math.prod(len(a.values) for a in rest)
    if per_value > study.max_passes_per_job:
        raise GridError(
            f"one value of {first.name} already needs {per_value} passes, more than max_passes_per_job "
            f"{study.max_passes_per_job}; reorder the inputs, shrink the grid or raise the limit"
        )
    width = study.max_passes_per_job // per_value
    chunks = tuple(
        Chunk(i, (first.slice(lo, lo + width), *rest))
        for i, lo in enumerate(range(0, len(first.values), width))
    )
    return Grid(axes, fixed, chunks)


def match_combo(chunk: Chunk, reported: dict[str, str]) -> tuple[int | str, ...] | None:
    """Map MT5-reported axis values (name -> text) to this chunk's grid values, or None."""
    out = []
    for a in chunk.axes:
        if a.name not in reported:
            return None
        i = a.index_of(reported[a.name])
        if i is None:
            return None
        out.append(a.values[i])
    return tuple(out)
