"""Fake terminal64 for integration tests (P1_PLAN §12).

Invoked as: fake_terminal.py /portable /config:<ini>
Behaviour is selected by the FAKE_MODE environment variable:
  ok             start line, telemetry, report, success line, exit 0
  no_start       never writes the start line (sleeps until killed)
  hang           start line, then sleeps until killed
  crash          start line, exit 3, no telemetry
  bad_profit     like ok but STAT_PROFIT disagrees with the deals
  no_success     like ok but the terminal log has no success line
  update         LiveUpdate handoff: log the /update line, relaunch itself (build 6251) with the
                 same ini after FAKE_DELAY seconds, and exit at once (seen on hardware, G1)
  update_none    LiveUpdate handoff line, but no relaunch
  update_hang    LiveUpdate handoff, and the relaunched terminal hangs
It also checks what the real terminal would need: an ASCII [Tester] ini and a
UTF-16LE .set in MQL5/Profiles/Tester that carries RL_JobId.

With Optimization=1 in the ini it behaves like the hardware runs in verification/p3 (S-HW-P3):
the full grid of the ||Y inputs, frames written by the "frame-mode instance" (out of pass order),
tester log lines, an optimization XML (rows sorted by Result) and a cache file. FAKE_OPT_MODE:
  ok, missing_pass, cache_hit, cloud, xml_mismatch, xml_bad_header, no_frames, bad_daily
"""

from __future__ import annotations

import codecs
import datetime as dt
import itertools
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import subprocess  # noqa: E402

from tests.telemetry_factory import (  # noqa: E402
    TRACKING,
    env_doc,
    stats_values,
    write_telemetry,
)


def log(data_dir: Path, message: str, source: str = "Tester") -> None:
    d = data_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (dt.date.today().strftime("%Y%m%d") + ".log")
    line = f"XX\t0\t{dt.datetime.now():%H:%M:%S.000}\t{source}\t{message}\r\n"
    with open(f, "ab") as fh:
        if fh.tell() == 0:
            fh.write(codecs.BOM_UTF16_LE)
        fh.write(line.encode("utf-16-le"))


def tester_log(data_dir: Path, message: str, source: str = "Tester") -> None:
    d = data_dir / "Tester" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (dt.date.today().strftime("%Y%m%d") + ".log")
    with open(f, "ab") as fh:
        if fh.tell() == 0:
            fh.write(codecs.BOM_UTF16_LE)
        fh.write(f"XX\t0\t{dt.datetime.now():%H:%M:%S.000}\t{source}\t{message}\r\n".encode("utf-16-le"))


def _grid(params: dict[str, str]) -> tuple[list[str], list[list[str]]]:
    names, values = [], []
    for name, raw in params.items():
        parts = raw.split("||")
        if len(parts) == 5 and parts[4] == "Y":
            start, step, stop = float(parts[1]), float(parts[2]), float(parts[3])
            n = round((stop - start) / step)
            vals = [start + k * step for k in range(n + 1)]
            names.append(name)
            values.append([str(int(v)) if float(v).is_integer() else format(v, ".15g") for v in vals])
    return names, values


def optimize(data_dir: Path, common: Path, kv: dict[str, str], params: dict[str, str], job_id: str,
             build: int) -> int:
    mode = os.environ.get("FAKE_OPT_MODE", "ok")
    names, values = _grid(params)
    # MT5 numbers passes with the FIRST input as the inner loop (X4)
    combos = [tuple(reversed(c)) for c in itertools.product(*reversed(values))]
    deposit = float(kv["Deposit"])
    start = dt.datetime.strptime(kv["FromDate"], "%Y.%m.%d")
    end = dt.datetime.strptime(kv["ToDate"], "%Y.%m.%d")
    expert_name = kv["Expert"].replace("\\", "/").rsplit("/", 1)[-1]
    cache = data_dir / "Tester" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cache_file = cache / f"{expert_name}.{kv['Symbol']}.{kv['Period']}.{start:%Y%m%d}.{end:%Y%m%d}.10.ABC.opt"
    n = len(combos)
    tester_log(data_dir, "Local network farm switched off")
    tester_log(data_dir, "Cloud servers switched off")
    if mode == "cache_hit" or cache_file.exists():
        tester_log(data_dir, f"optimization already processed, total passes {n}")
        frames = []
    else:
        tester_log(data_dir, "complete optimization started")
        frames = list(enumerate(combos))
        if mode == "missing_pass":
            frames = frames[:-1]
        cloud = 2 if mode == "cloud" else 0
        tester_log(data_dir, f"optimization finished, total passes {n}")
        tester_log(data_dir, f"local {n - cloud} tasks (100%), remote 0 tasks (0%), cloud {cloud} tasks (0%)",
                   "Statistics")
        cache_file.write_bytes(b"cache")

    def result(combo):
        x = [float(v) for v in combo]
        profit = round((x[0] - x[-1] / 4) * 0.37, 2)
        return profit, 10 + int(x[0]) % 7

    prefix = f"RL_{job_id}_"
    if mode != "no_frames":
        (common / f"{prefix}fm_init.json").write_text(json.dumps(
            {"job_id": job_id, "telemetry_version": "3", "terminal_build": build, "mql_frame_mode": True}))
        lines, daily = [], ["pass,date,balance_close,equity_close,equity_min,equity_max,positions_max,ticks"]
        days = [start, start + dt.timedelta(days=1), end - dt.timedelta(days=1)]
        for pass_no, combo in reversed(frames):  # frames arrive out of pass order (X4)
            profit, trades = result(combo)
            stats = {k: 0.0 for k in ("gross_profit", "gross_loss", "balance_dd", "equity_dd", "profit_factor",
                                      "expected_payoff", "recovery_factor", "sharpe_ratio", "max_conwins",
                                      "max_conprofit_trades", "max_conlosses", "max_conloss_trades")}
            stats.update({"initial_deposit": deposit, "profit": profit, "trades": trades, "deals": 2 * trades,
                          "profit_trades": trades // 2, "loss_trades": trades - trades // 2,
                          "gross_profit": max(profit, 0) + 5, "gross_loss": min(profit, 0) - 5})
            lines.append(json.dumps({
                "pass": pass_no, "format": 3, "inputs": [f"{a}={v}" for a, v in zip(names, combo, strict=True)]
                + ["InpLots=0.01"], "stats": stats,
                "tracking": {"equity_peak": deposit + 6, "equity_min": deposit - 4, "equity_max_dd": 7.5,
                             "equity_max_dd_pct": 0.075, "max_floating_loss": -3.0, "max_positions": 1,
                             "max_lots": 0.01, "min_margin_level": None, "stop_out_deals": 0},
                "ticks": 5000, "first_tick": f"{start:%Y.%m.%d} 00:05:00",
                "last_tick": f"{end - dt.timedelta(days=1):%Y.%m.%d} 22:00:00", "bars": 100, "days": 3}))
            for k, day in enumerate(days, start=1):
                eq = deposit + profit * k / 3
                if mode == "bad_daily" and k == 3:
                    eq += 1
                daily.append(f"{pass_no},{day:%Y.%m.%d},{eq:.2f},{eq:.2f},{eq - 1:.2f},{eq + 1:.2f},1,100")
        (common / f"{prefix}passes.jsonl").write_text("".join(ln + "\n" for ln in lines))
        (common / f"{prefix}pdaily.csv").write_text("\n".join(daily) + "\n")
        (common / f"{prefix}fm_done.json").write_text(json.dumps(
            {"job_id": job_id, "telemetry_version": "3", "mql_frame_mode": True, "frames_received": len(lines),
             "frames_on_rescan": len(lines), "pass_events": len(lines), "duplicate_passes": 0, "bad_frames": 0,
             "write_failures": 0}))

    # XML report: always written, even for a cache hit (X6)
    header = ["Pass", "Result", "Profit", "Expected Payoff", "Profit Factor", "Recovery Factor", "Sharpe Ratio",
              "Custom", "Equity DD %", "Trades", *names]
    if mode == "xml_bad_header":
        header[2] = "Gewinn"
    rows = []
    for pass_no, combo in enumerate(combos):
        profit, trades = result(combo)
        if mode == "xml_mismatch" and pass_no == 0:
            profit += 1
        rows.append([str(pass_no), f"{deposit + profit:.2f}", f"{profit:.2f}", "0.1", "1.1", "0.5", "0.2", "0", "0.3",
                     str(trades), *combo])
    rows.sort(key=lambda r: -float(r[1]))

    def xml_row(cells):
        return "<Row>" + "".join(f'<Cell><Data ss:Type="String">{c}</Data></Cell>' for c in cells) + "</Row>\n"

    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
           'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
           '<Worksheet ss:Name="Tester Optimizator Results"><Table>\n'
           + xml_row(header) + "".join(xml_row(r) for r in rows) + "</Table></Worksheet></Workbook>\n")
    (data_dir / f"{kv['Report']}.xml").write_bytes(xml.encode("utf-8"))
    log(data_dir, "optimization frame expert loaded successfully", "Experts")
    return 0


def main() -> int:
    mode = os.environ.get("FAKE_MODE", "ok")
    data_dir = Path(sys.argv[0]).absolute().parent  # do not follow the test symlink
    common = Path(os.environ["FAKE_COMMON_DIR"])
    cfg = next(a for a in sys.argv[1:] if a.startswith("/config:"))[len("/config:"):]
    ini = Path(cfg).read_bytes().decode("ascii")
    kv = dict(line.split("=", 1) for line in ini.splitlines() if "=" in line)
    assert ini.startswith("[Tester]"), "not a tester ini"
    raw = (data_dir / "MQL5" / "Profiles" / "Tester" / kv["ExpertParameters"]).read_bytes()
    assert raw.startswith(codecs.BOM_UTF16_LE), ".set must be UTF-16LE with BOM"
    params = dict(line.split("=", 1) for line in raw[2:].decode("utf-16-le").splitlines() if "=" in line)
    job_id = params["RL_JobId"]
    assert (data_dir / "MQL5" / "Experts" / (kv["Expert"].replace("\\", "/") + ".ex5")).is_file()

    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    build = int(os.environ.get("FAKE_BUILD", "6230"))
    log(data_dir, f"MetaTrader 5 x64 build {build} started for MetaQuotes Ltd.", "Terminal")
    if mode in ("update", "update_none", "update_hang"):
        log(data_dir, f'start "C:\\x\\liveupdate\\terminal64.exe" /update /path:"{data_dir}" /portable /config:"{cfg}"',
            "LiveUpdate")
        if mode != "update_none":
            child = {**os.environ, "FAKE_MODE": "hang" if mode == "update_hang" else "ok",
                     "FAKE_DELAY": "0.5", "FAKE_BUILD": "6251"}
            terminal = data_dir / "terminal64.exe"
            # the extra argument makes the child findable by its terminal path, like the real relaunch
            subprocess.Popen([str(terminal), "/portable", f"/config:{cfg}", "/relaunched", str(terminal)],
                             env=child, start_new_session=True)
        return 0
    if mode == "no_start":
        time.sleep(3600)
        return 0
    log(data_dir, "automatic testing started")
    if kv.get("Optimization") == "1" and mode == "ok":
        return optimize(data_dir, common, kv, params, job_id, build)
    if mode == "hang":
        time.sleep(3600)
        return 0
    if mode == "crash":
        return 3
    agent_logs = data_dir / "Tester" / "Agent-127.0.0.1-3000" / "logs"
    agent_logs.mkdir(parents=True, exist_ok=True)
    (agent_logs / "agent.log").write_bytes(codecs.BOM_UTF16_LE + "agent ok\r\n".encode("utf-16-le"))
    stats = stats_values(profit=9.71) if mode == "bad_profit" else None
    version = os.environ.get("FAKE_TELEMETRY_VERSION", "1")
    extra = {"tracking": TRACKING} if version in ("2", "3") else {}
    write_telemetry(common, job_id, stats=stats, version=version,
                    env=env_doc(job_id, terminal_build=build, telemetry_version=version, **extra))
    (data_dir / f"{kv['Report']}.htm").write_bytes(codecs.BOM_UTF16_LE + "<html>report</html>".encode("utf-16-le"))
    if mode != "no_success":
        log(data_dir, 'last test passed with result "successfully finished" in 0:00:00.216')
    return 0


if __name__ == "__main__":
    sys.exit(main())
