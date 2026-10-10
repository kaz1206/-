"""Builds P1 telemetry files for tests, based on the real hardware deals (tests/fixtures/hw_20261009)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DEALS = Path(__file__).parent / "fixtures" / "hw_20261009" / "deals.csv"


def stats_values(**over):
    v = {
        "initial_deposit": 10000.00, "profit": 9.69, "gross_profit": 32.01, "gross_loss": -22.32,
        "trades": 32.0, "deals": 64.0, "balance_dd": 7.43, "equity_dd": 5.07, "profit_factor": 1.43,
        "expected_payoff": 0.30, "recovery_factor": 1.91, "sharpe_ratio": 14.51,
        "profit_trades": 16.0, "loss_trades": 16.0, "max_conwins": 5.14, "max_conprofit_trades": 6.0,
        "max_conlosses": -1.23, "max_conloss_trades": 3.0,
    }
    v.update(over)
    return v


TRACKING = {"equity_peak": 10013.0, "equity_min": 9995.0, "equity_max_dd": 5.07, "equity_max_dd_pct": 0.05,
            "max_floating_loss": -4.0, "max_positions": 2, "max_lots": 0.02, "min_margin_level": 92255.3,
            "stop_out_deals": 0}


def daily_text_from_deals(deals_text: str, initial: float = 10000.0) -> str:
    """Synthetic daily rows consistent with the deals (equity close = balance close at each day end)."""
    rows = [line.split(",") for line in deals_text.splitlines()[1:] if line]
    by_day: dict[str, float] = {}
    bal = initial
    for r in rows:
        if r[3] in ("0", "1"):
            bal += sum(float(r[i]) for i in (7, 8, 9, 10))
        by_day[r[1][:10]] = bal
    out = ["date,balance_close,equity_close,equity_min,equity_max,positions_max,ticks"]
    for day, b in sorted(by_day.items()):
        out.append(f"{day},{b:.2f},{b:.2f},{b - 1:.2f},{b + 1:.2f},2,1000")
    return "\n".join(out) + "\n"


def env_doc(job_id: str, **over):
    e = {
        "telemetry_version": "1", "job_id": job_id, "terminal_build": 6230,
        "terminal_data_path": "C:\\MT5\\Tester\\Agent-127.0.0.1-3000", "mql_tester": True,
        "account_server": "MetaQuotes-Demo", "account_company": "MetaQuotes Ltd.",
        "account_currency": "USD", "account_leverage": 100, "initial_balance": 10000.00,
        "period": 16385, "period_name": "PERIOD_H1",
        "symbol": {"name": "EURUSD", "digits": 5, "point": 0.00001, "contract_size": 100000.0,
                   "tick_value": 1.0, "tick_size": 0.00001, "spread_float": True, "swap_long": -0.7,
                   "swap_short": -1.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 500.0,
                   "currency_profit": "USD", "currency_margin": "EUR"},
        "observed": {"spread": 12},
        "ticks": 467749, "bars": 96,
        "first_tick": "2023.01.09 00:02:40", "first_tick_msc": 1673222560082,
        "last_tick": "2023.01.12 23:59:52", "last_tick_msc": 1673567992042,
        "uninit_reason": 1,
    }
    e.update(over)
    return e


def write_telemetry(
    common_dir: Path,
    job_id: str,
    *,
    deals_text: str | None = None,
    stats: dict | None = None,
    env: dict | None = None,
    done: dict | None = None,
    skip: tuple[str, ...] = (),
    version: str = "1",
    daily_text: str | None = None,
) -> None:
    common_dir.mkdir(parents=True, exist_ok=True)
    deals_text = deals_text if deals_text is not None else FIXTURE_DEALS.read_text(encoding="utf-8")
    rows = [line for line in deals_text.splitlines()[1:] if line]
    trade = sum(1 for line in rows if len(line.split(",")) > 3 and line.split(",")[3] in ("0", "1"))
    docs = {
        "deals.csv": deals_text,
        "stats.json": json.dumps({"source": "OnDeinit", "values": stats or stats_values()}),
        "env.json": json.dumps(env or env_doc(job_id, telemetry_version=version,
                                              **({"tracking": TRACKING} if version == "2" else {})),
                               ensure_ascii=False),
        "done.json": json.dumps(done or {"job_id": job_id, "telemetry_version": version,
                                         "deals_total": len(rows), "trade_deals": trade}),
    }
    if version == "2":
        docs["daily.csv"] = daily_text if daily_text is not None else daily_text_from_deals(deals_text)
    for suffix, text in docs.items():
        if suffix.split(".")[0] in skip:
            continue
        (common_dir / f"RL_{job_id}_{suffix}").write_text(text, encoding="utf-8")
