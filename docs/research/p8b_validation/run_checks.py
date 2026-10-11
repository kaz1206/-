"""P8B_PLAN §2.4: re-run C3 and C4 of oos_band_check.py with the PRODUCTION band (robustlab.stats.validation.oos_band,
fix1), same conditions and seeds. The draft-1 formula ("orig") is kept from the check script for the side-by-side.
Usage: uv run python docs/research/p8b_validation/run_checks.py c3:<kind> c4:<kind>:<mu> ...
"""
import importlib.util
import sys
from pathlib import Path

from robustlab.stats import validation as ev

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs/research/p8_simulations"))
spec = importlib.util.spec_from_file_location("obc", ROOT / "docs/research/p8_simulations/oos_band_check.py")
obc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(obc)
_orig = obc.band


def band(e, n_oos=obc.N_OOS):
    b = _orig(e, n_oos)
    p = ev.oos_band(e, n_oos)
    b.update({"se_v": p["se_val"], "se_o1": p["se_oos"], "lower1": p["lower"]})
    return b


obc.band = band

if __name__ == "__main__":
    for case in sys.argv[1:]:
        name, *args = case.split(":")
        {"c3": obc.c3, "c4": obc.c4}[name](*args)
