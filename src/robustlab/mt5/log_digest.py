"""A safe digest of a job's terminal log for diagnosing start failures (F8 still holds: no log body is printed).

Only lines whose message matches a known template (start / config / build / update / exit / connection / error
words) are shown, and every shown line is masked: IPv4 / IPv6 addresses and every run of 5 or more digits (account
logins, ports, pids) are replaced. All other lines are only counted. The logs are UTF-16LE, tab separated:
code, level, time, source, message [S-HW-P1, W6].
"""

from __future__ import annotations

import re

KNOWN = [re.compile(p, re.IGNORECASE) for p in (
    r"MetaTrader 5 x64 build \d+ started", r"start config", r"cannot load config", r"automatic testing started",
    r"last test passed", r"exit with code", r"shutdown with", r"liveupdate", r"new version", r"\bupdate",
    r"authoriz", r"connect", r"login", r"account", r"expert", r"tester", r"symbol", r"history", r"terminal",
    r"error", r"failed", r"cannot", r"invalid", r"not found", r"denied", r"expired", r"already", r"license",
    r"disabled", r"stopped", r"timeout")]
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
IPV6 = re.compile(r"\b(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.IGNORECASE)  # 3+ colons: not a clock time
DIGITS = re.compile(r"\d{5,}")


def mask(s: str) -> str:
    s = IPV4.sub("x.x.x.x", s)
    s = IPV6.sub("x:x:x", s)
    return DIGITS.sub("#####", s)


def digest(text: str, max_lines: int = 60) -> tuple[list[str], int]:
    """(masked known lines as 'time source: message', number of lines not shown)."""
    shown, hidden = [], 0
    for raw in text.splitlines():
        parts = raw.split("\t")
        if len(parts) < 5:
            if raw.strip():
                hidden += 1
            continue
        t, source, msg = parts[2], parts[3], "\t".join(parts[4:])
        if any(k.search(msg) or k.search(source) for k in KNOWN) and len(shown) < max_lines:
            shown.append(mask(f"{t[-12:]} {source}: {msg}"))
        else:
            hidden += 1
    return shown, hidden
