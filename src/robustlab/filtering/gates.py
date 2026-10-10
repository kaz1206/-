"""Initial-filter gates (ARCHITECTURE §3.3, P4_PLAN §3, C51-C54).

Every gate is evaluated for every candidate - never stop at the first failure - and each gate reports
its value, threshold and status. A value that cannot be computed never counts as a pass (except where
"not applicable" is the honest answer, e.g. no margin level because no position was ever held).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from robustlab.core.models import StrategyProfile
from robustlab.stats.psr import PSR_VERSION, min_trl, psr

FILTER_COMPONENT = "initial_filter"
FILTER_VERSION = "f1"
PSR_INPUT_DEFINITION = "C53"  # r_t = (E_t - E_{t-1}) / D, days with ticks, ddof=1, not annualized

PASS, FAIL, WARN, NA = "PASS", "FAIL", "WARN", "NOT_APPLICABLE"

# code -> (category, description, severity)
REASON_CODES: dict[str, tuple[str, str, str]] = {
    "BANKRUPTCY": ("bankruptcy", "equity fell to zero or below", "FAIL"),
    "STOP_OUT": ("bankruptcy", "at least one stop-out deal", "FAIL"),
    "MARGIN_LEVEL_LOW": ("risk", ("minimum margin level below the profile threshold, at the round's standard "
                                  "conditions (not a safety judgement, C52)"), "FAIL"),
    "MAX_POSITIONS_EXCEEDED": ("risk", "more simultaneous positions than the profile allows", "FAIL"),
    "INSUFFICIENT_TRADES": ("statistical_sufficiency", "fewer closed trades than the safety floor", "FAIL"),
    "INSUFFICIENT_DATA": ("statistical_sufficiency", ("fewer daily observations than needed for "
                                                      "skewness/kurtosis"), "FAIL"),
    "NEGATIVE_EXPECTANCY": ("expectancy", "net profit is zero or negative", "FAIL"),
    "LOW_PSR": ("statistical_sufficiency", ("PSR(SR*) below the threshold that supports a true Sharpe above SR*, "
                                            "or PSR not computable (C54)"), "FAIL"),
    "TRADE_FREQUENCY_OUT_OF_PROFILE": ("profile", "trades per year outside the profile's expected range", "WARN"),
}


@dataclass(frozen=True)
class Gate:
    gate: str
    code: str
    status: str
    value: Any
    threshold: Any
    note: str = ""


@dataclass(frozen=True)
class FilterDecision:
    outcome: str  # PASS / FAIL
    reasons: list[str]
    warnings: list[str]
    gates: list[Gate]
    values: dict[str, Any]

    def gates_json(self) -> list[dict[str, Any]]:
        return [asdict(g) for g in self.gates]


def _num(m: dict[str, Any], key: str) -> float | None:
    v = m.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _missing(gate: str, code: str, key: str, threshold: Any) -> Gate:
    return Gate(gate, code, FAIL, None, threshold, f"{key} is missing or not finite")


def evaluate(m: dict[str, Any], profile: StrategyProfile, years: float) -> FilterDecision:
    gates: list[Gate] = []

    # 1. bankruptcy
    if not isinstance(m.get("bankrupt"), bool):
        gates.append(_missing("bankruptcy", "BANKRUPTCY", "bankrupt", "equity_min > 0"))
    else:
        gates.append(Gate("bankruptcy", "BANKRUPTCY", FAIL if m["bankrupt"] else PASS, m["bankrupt"], "equity_min > 0"))

    # 2. stop-out
    so = _num(m, "stop_out_deals")
    gates.append(_missing("stop_out", "STOP_OUT", "stop_out_deals", 0) if so is None
                 else Gate("stop_out", "STOP_OUT", FAIL if so > 0 else PASS, int(so), 0))

    # 3. margin level at the round's standard conditions (C52)
    if m.get("min_margin_level") is None and "min_margin_level" in m:
        gates.append(Gate("margin_level", "MARGIN_LEVEL_LOW", NA, None, profile.min_margin_level_pct,
                          "no position was ever held"))
    else:
        ml = _num(m, "min_margin_level")
        gates.append(_missing("margin_level", "MARGIN_LEVEL_LOW", "min_margin_level", profile.min_margin_level_pct)
                     if ml is None else
                     Gate("margin_level", "MARGIN_LEVEL_LOW", FAIL if ml < profile.min_margin_level_pct else PASS,
                          ml, profile.min_margin_level_pct, "at the round's standard conditions"))

    # 4. simultaneous positions
    mp = _num(m, "max_positions")
    if profile.max_positions is None:
        gates.append(Gate("max_positions", "MAX_POSITIONS_EXCEEDED", NA, None if mp is None else int(mp), None,
                          "no limit in this profile (recorded only)"))
    elif mp is None:
        gates.append(_missing("max_positions", "MAX_POSITIONS_EXCEEDED", "max_positions", profile.max_positions))
    else:
        gates.append(Gate("max_positions", "MAX_POSITIONS_EXCEEDED", FAIL if mp > profile.max_positions else PASS,
                          int(mp), profile.max_positions))

    # 5. trades (safety floor; the statistical test is PSR)
    tr = _num(m, "trades")
    gates.append(_missing("trades", "INSUFFICIENT_TRADES", "trades", profile.min_trades) if tr is None
                 else Gate("trades", "INSUFFICIENT_TRADES", FAIL if tr < profile.min_trades else PASS, int(tr),
                           profile.min_trades))

    # 6. daily observations
    days = _num(m, "days")
    gates.append(_missing("days", "INSUFFICIENT_DATA", "days", profile.min_days) if days is None
                 else Gate("days", "INSUFFICIENT_DATA", FAIL if days < profile.min_days else PASS, int(days),
                           profile.min_days))

    # 7. expectancy
    np_ = _num(m, "net_profit")
    gates.append(_missing("expectancy", "NEGATIVE_EXPECTANCY", "net_profit", "> 0") if np_ is None
                 else Gate("expectancy", "NEGATIVE_EXPECTANCY", FAIL if np_ <= 0 else PASS, np_, "> 0"))

    # 8. PSR (C53 inputs, C54 meaning)
    sr, sk, ku = _num(m, "daily_sharpe"), _num(m, "daily_skewness"), _num(m, "daily_kurtosis")
    n = int(days) if days is not None else None
    p = psr(sr, n, sk, ku, profile.psr_sr_star)
    if p.value is None:
        gates.append(Gate("psr", "LOW_PSR", FAIL, None, profile.psr_threshold, f"PSR not computable: {p.reason}"))
    else:
        gates.append(Gate("psr", "LOW_PSR", FAIL if p.value < profile.psr_threshold else PASS, p.value,
                          profile.psr_threshold, f"PSR(SR*={profile.psr_sr_star})"))
    t = min_trl(sr, sk, ku, profile.psr_sr_star, profile.psr_threshold)

    # 9. trade frequency (WARN only, M6)
    lo, hi = profile.trades_per_year
    if tr is None or years <= 0:
        gates.append(Gate("trade_frequency", "TRADE_FREQUENCY_OUT_OF_PROFILE", NA, None, [lo, hi],
                          "trades or period unknown"))
    else:
        per_year = tr / years
        gates.append(Gate("trade_frequency", "TRADE_FREQUENCY_OUT_OF_PROFILE",
                          WARN if not lo <= per_year <= hi else PASS, round(per_year, 2), [lo, hi]))

    reasons = [g.code for g in gates if g.status == FAIL]
    warnings = [g.code for g in gates if g.status == WARN]
    values = {
        "psr": p.value, "psr_reason": p.reason, "psr_sr_star": profile.psr_sr_star, "psr_version": PSR_VERSION,
        "psr_input_definition": PSR_INPUT_DEFINITION, "sharpe": sr, "skewness": sk, "kurtosis": ku, "n": n,
        "min_trl": t.value, "min_trl_reason": t.reason,
        "extra_days_needed": (max(0, math.ceil(t.value) - n) if t.value is not None and n is not None else None),
        "equity_max_dd": m.get("equity_max_dd"), "equity_max_dd_pct": m.get("equity_max_dd_pct"),
        "balance_max_dd": m.get("balance_max_dd"),  # recorded, not filtered (C51)
    }
    return FilterDecision(FAIL if reasons else PASS, reasons, warnings, gates, values)
