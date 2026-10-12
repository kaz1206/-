"""Copy research EAs (and Telemetry.mqh) into the portable MT5 and compile them with MetaEditor, in one command.

Usage (Windows):  .\\.venv\\Scripts\\python.exe scripts\\install_eas.py RL_Donchian RL_TSMom RL_BollingerMR
                  .\\.venv\\Scripts\\python.exe scripts\\install_eas.py --script RL_ImportT1   (scripts under Scripts\\RobustLab)

The same steps as docs/runbooks/P3_E2E.md §2 (copy, `metaeditor64.exe /portable /compile:... /log:...`, read the
log for "Result: 0 errors"; MetaEditor's exit code is 1 even on success, so it is not used). The terminal folder comes
from configs/terminal.yaml (terminal_path / data_dir). Only the named EAs are copied and compiled; other EAs (for
example RL_SmokeTest, whose .ex5 identifies earlier research) are not touched. Stops at the first EA with errors.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from robustlab.config.loader import load_terminal_config

REPO = Path(__file__).resolve().parents[1]


def read_log(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-16", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("experts", nargs="*", help="EA names under mql5/Experts/RobustLab (without .mq5)")
    ap.add_argument("--script", action="append", default=[], help="script name under mql5/Scripts/RobustLab")
    ap.add_argument("--terminal", type=Path, default=REPO / "configs" / "terminal.yaml")
    a = ap.parse_args()

    cfg = load_terminal_config(a.terminal)
    data = cfg.data_dir if cfg.data_dir is not None else cfg.terminal_path.parent
    editor = cfg.terminal_path.parent / "metaeditor64.exe"
    if not editor.exists():
        print(f"MetaEditor not found: {editor}")
        return 1
    inc_dst = data / "MQL5" / "Include" / "RobustLab"
    exp_dst = data / "MQL5" / "Experts" / "RobustLab"
    inc_dst.mkdir(parents=True, exist_ok=True)
    exp_dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "mql5" / "Include" / "RobustLab" / "Telemetry.mqh", inc_dst / "Telemetry.mqh")
    print(f"copied Telemetry.mqh -> {inc_dst}")
    if not a.experts and not a.script:
        print("nothing to install")
        return 1
    scr_dst = data / "MQL5" / "Scripts" / "RobustLab"
    scr_dst.mkdir(parents=True, exist_ok=True)
    todo = [(n, "Experts", exp_dst) for n in a.experts] + [(n, "Scripts", scr_dst) for n in a.script]
    for name, kind, dst_dir in todo:
        src = REPO / "mql5" / kind / "RobustLab" / f"{name}.mq5"
        if not src.exists():
            print(f"{name}: {src} does not exist")
            return 1
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        log = REPO / f"compile_{name}.log"
        log.unlink(missing_ok=True)
        subprocess.run([str(editor), "/portable", f"/compile:{dst}", f"/log:{log}"], check=False)
        if not log.exists():
            print(f"{name}: MetaEditor wrote no log ({log})")
            return 2
        lines = [ln.strip() for ln in read_log(log).splitlines() if "Result" in ln or "error" in ln.lower()]
        for ln in lines:
            print(f"{name}: {ln}")
        ex5 = dst.with_suffix(".ex5")
        if not any("Result: 0 errors" in ln for ln in lines) or not ex5.exists():
            print(f"{name}: COMPILE FAILED - stop here and send these lines")
            return 2
        print(f"{name}: OK -> {ex5}")
    print("all compiled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
