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
"""

from __future__ import annotations

import codecs
import datetime as dt
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import subprocess  # noqa: E402

from tests.telemetry_factory import env_doc, stats_values, write_telemetry  # noqa: E402


def log(data_dir: Path, message: str, source: str = "Tester") -> None:
    d = data_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / (dt.date.today().strftime("%Y%m%d") + ".log")
    line = f"XX\t0\t{dt.datetime.now():%H:%M:%S.000}\t{source}\t{message}\r\n"
    with open(f, "ab") as fh:
        if fh.tell() == 0:
            fh.write(codecs.BOM_UTF16_LE)
        fh.write(line.encode("utf-16-le"))


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
    if mode == "hang":
        time.sleep(3600)
        return 0
    if mode == "crash":
        return 3
    agent_logs = data_dir / "Tester" / "Agent-127.0.0.1-3000" / "logs"
    agent_logs.mkdir(parents=True, exist_ok=True)
    (agent_logs / "agent.log").write_bytes(codecs.BOM_UTF16_LE + "agent ok\r\n".encode("utf-16-le"))
    stats = stats_values(profit=9.71) if mode == "bad_profit" else None
    write_telemetry(common, job_id, stats=stats, env=env_doc(job_id, terminal_build=build))
    (data_dir / f"{kv['Report']}.htm").write_bytes(codecs.BOM_UTF16_LE + "<html>report</html>".encode("utf-16-le"))
    if mode != "no_success":
        log(data_dir, 'last test passed with result "successfully finished" in 0:00:00.216')
    return 0


if __name__ == "__main__":
    sys.exit(main())
