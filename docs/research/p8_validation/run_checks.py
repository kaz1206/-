"""P8_PLAN §8.1: re-run the pre-implementation checks H1-H4 with the PRODUCTION functions, same conditions and seed.

validation_gate2.py is loaded and its p-value and Holm step are replaced by robustlab.stats.validation.evidence /
holm. Everything else (data generation, seeds, counts, B = 200, criteria) is unchanged. The output is compared
line by line with docs/research/p8_simulations/validation_gate2_output_2026-10-11.txt.
Usage: uv run python docs/research/p8_validation/run_checks.py <case> ...   (cases as in validation_gate2.py)
"""
import importlib.util
import sys
from pathlib import Path

from robustlab.stats import validation as ev

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("vg2", ROOT / "docs/research/p8_simulations/validation_gate2.py")
vg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vg)


def p_value(segs, rng):
    return ev.evidence(segs, vg.B, rng)["p"]


def holm(ps, alpha=vg.ALPHA, ids=None):
    res = ev.holm({f"{i:02d}": p for i, p in enumerate(ps)}, alpha)
    return [res[f"{i:02d}"] for i in range(len(ps))]


vg.p_value, vg.holm = p_value, holm

if __name__ == "__main__":
    for case in sys.argv[1:]:
        name, *args = case.split(":")
        {"h1": vg.h1, "h2": vg.h2, "h3": vg.h3, "h4": vg.h4}[name](*args)
