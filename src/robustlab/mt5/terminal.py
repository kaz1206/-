"""MT5 terminal process control for single tests (ARCHITECTURE §14.4, P1_PLAN §15 F3/F7).

- Launch: terminal64.exe /portable /config:"<ini>"   (W1)
- Start:  the terminal log gets "automatic testing started"
- Result: the terminal log gets 'last test passed with result "successfully finished"'
Logs are UTF-16LE, appended per day, so only bytes written after launch are read (W6).

Auto-update handoff (G1/G2, found in the first hardware E2E run): the launched terminal may
start "liveupdate\\terminal64.exe /update ... /config:<ini>" and exit at once; the updated
terminal is then relaunched with the same ini. We keep waiting for it, and we never return
while any process of this terminal is still running.
"""

from __future__ import annotations

import enum
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

START_PATTERN = "automatic testing started"
SUCCESS_PATTERN = 'last test passed with result "successfully finished"'
UPDATE_RE = re.compile(r"LiveUpdate\s+start\b.*?/update\b")
BUILD_RE = re.compile(r"MetaTrader 5 x64 build (\d+) started")


class TerminalOutcomeKind(str, enum.Enum):
    EXITED = "EXITED"
    FAILED_TO_START = "FAILED_TO_START"
    TIMED_OUT = "TIMED_OUT"


@dataclass
class TerminalOutcome:
    kind: TerminalOutcomeKind
    pid: int | None
    exit_code: int | None
    started: bool
    succeeded_line: bool
    log_segments: dict[Path, bytes] = field(default_factory=dict)
    seconds: float = 0.0
    updated: bool = False  # the terminal handed over to LiveUpdate during the job (G1)
    builds: list[int] = field(default_factory=list)  # builds that started during the job, in order


def log_dirs(data_dir: Path) -> list[Path]:
    dirs = [data_dir / "logs", data_dir / "Tester" / "logs"]
    tester = data_dir / "Tester"
    if tester.is_dir():
        dirs += sorted(p / "logs" for p in tester.glob("Agent-*") if p.is_dir())
    return dirs


def snapshot_logs(dirs: list[Path]) -> dict[Path, int]:
    sizes: dict[Path, int] = {}
    for d in dirs:
        if d.is_dir():
            for f in d.glob("*.log"):
                sizes[f] = f.stat().st_size
    return sizes


def read_new_bytes(dirs: list[Path], snapshot: dict[Path, int]) -> dict[Path, bytes]:
    out: dict[Path, bytes] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.log")):
            start = snapshot.get(f, 0)
            start -= start % 2  # UTF-16 code unit boundary
            try:
                size = f.stat().st_size
                if size <= start:
                    continue
                with open(f, "rb") as fh:
                    fh.seek(start)
                    out[f] = fh.read(size - start)
            except OSError:
                continue
    return out


def decode_log(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        data = data[2:]
    if len(data) % 2:
        data = data[:-1]  # a write in progress
    return data.decode("utf-16-le", errors="replace")


def _same_path(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def find_running(terminal_path: Path) -> list[int]:
    """PIDs of processes running this terminal executable (P1 refuses to start then, W10)."""
    pids = []
    for p in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            exe = p.info.get("exe")
            cmd = p.info.get("cmdline") or []
            if (exe and _same_path(exe, terminal_path)) or any(_same_path(c, terminal_path) for c in cmd):
                if p.pid != os.getpid():
                    pids.append(p.pid)
        except (psutil.Error, OSError):
            continue
    return pids


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=10)


def _launch(terminal_path: Path, ini_path: Path) -> subprocess.Popen:
    # The terminal's working directory is its own folder, so every path must be absolute.
    terminal_path, ini_path = terminal_path.resolve(), ini_path.resolve()
    if os.name == "nt":
        # Exactly the quoting verified on hardware: /config:"<path>"
        cmd = f'"{terminal_path}" /portable /config:"{ini_path}"'
    else:  # tests (fake terminal)
        cmd = [str(terminal_path), "/portable", f"/config:{ini_path}"]
    return subprocess.Popen(cmd, cwd=str(terminal_path.parent))


def run_terminal(
    terminal_path: Path,
    data_dir: Path,
    ini_path: Path,
    *,
    start_timeout_sec: float,
    run_timeout_sec: float,
    poll_interval_sec: float,
    on_launch: Callable[[int], None] | None = None,
) -> TerminalOutcome:
    dirs = log_dirs(data_dir)
    snapshot = snapshot_logs(dirs)
    term_dir = [data_dir / "logs"]
    t0 = time.monotonic()
    proc = _launch(terminal_path, ini_path)
    if on_launch:
        on_launch(proc.pid)

    started = False
    updated = False
    relaunch_seen = False
    start_deadline = t0 + start_timeout_sec
    deadline = t0 + run_timeout_sec
    kind = TerminalOutcomeKind.EXITED
    while True:
        now = time.monotonic()
        text = "".join(decode_log(b) for b in read_new_bytes(term_dir, snapshot).values())
        started = START_PATTERN in text
        if not updated and UPDATE_RE.search(text):
            updated = True
            start_deadline = now + start_timeout_sec  # give the relaunched terminal its own start window
        own_alive = proc.poll() is None
        others = [] if own_alive else find_running(terminal_path)
        if not own_alive and others:
            relaunch_seen = True
        if not own_alive and not others:
            # G1: after a LiveUpdate handoff, wait for the relaunched terminal unless it already finished
            waiting_for_relaunch = updated and not relaunch_seen and SUCCESS_PATTERN not in text
            if not (waiting_for_relaunch and now <= start_deadline):
                break
        if not started and now > start_deadline:
            kind = TerminalOutcomeKind.FAILED_TO_START
            break
        if now > deadline:
            kind = TerminalOutcomeKind.TIMED_OUT
            break
        time.sleep(poll_interval_sec)

    if kind is not TerminalOutcomeKind.EXITED:
        kill_tree(proc.pid)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    # G2: never hand back control while any process of this terminal is alive
    for pid in find_running(terminal_path):
        kill_tree(pid)

    segments = read_new_bytes(log_dirs(data_dir), snapshot)  # agent folders may appear during the run
    term_text = "".join(decode_log(b) for p, b in segments.items() if p.parent == data_dir / "logs")
    started = started or START_PATTERN in term_text
    if kind is TerminalOutcomeKind.EXITED and not started:
        kind = TerminalOutcomeKind.FAILED_TO_START
    return TerminalOutcome(
        kind=kind,
        pid=proc.pid,
        exit_code=proc.returncode if kind is TerminalOutcomeKind.EXITED else None,
        started=started,
        succeeded_line=SUCCESS_PATTERN in term_text,
        log_segments=segments,
        seconds=round(time.monotonic() - t0, 3),
        updated=updated or bool(UPDATE_RE.search(term_text)),
        builds=[int(b) for b in BUILD_RE.findall(term_text)],
    )
