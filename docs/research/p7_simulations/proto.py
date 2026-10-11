"""P7 feasibility prototype: region+pareto selector vs naive max / hard-gate top-K on synthetic score grids."""
import sys
import numpy as np
from scipy.ndimage import gaussian_filter
from robustlab.robustness import grid_stats as gs

S, K, THR = 30, 5, 0.05

def make(rng, plateau=True, spikes=3, noise_sd=0.02):
    f = gaussian_filter(rng.standard_normal((S, S)), 2.0)
    f = f / f.std() * noise_sd
    plat = np.zeros((S, S), bool)
    if plateau:
        i, j = rng.integers(2, S - 8, 2)
        plat[i:i + 6, j:j + 6] = True
        f[plat] += 0.08
    spk = np.zeros((S, S), bool)
    while spk.sum() < spikes:
        i, j = rng.integers(1, S - 1, 2)
        if not plat[max(0,i-2):i+3, max(0,j-2):j+3].any():
            spk[i, j] = True
    f[spk] = 0.15
    return f, plat, spk

def dominates(a, b):
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))

def select(f, rng):
    passed = f > THR
    r = gs.compute(f, np.full(f.shape, "OK"), passed, ["a", "b"])
    dd = {c: rng.uniform() for c in r.per_cell}
    reps = []
    for reg in r.regions:
        ok = [c for c in reg["members"] if r.per_cell[c]["robust_score"] is not None and r.per_cell[c]["robust_score"] > 0
              and (r.per_cell[c]["peak_ratio"] is None or r.per_cell[c]["peak_ratio"] <= 1.5)]
        if not ok:
            continue
        med = reg["medoid"]
        reps.append(min(ok, key=lambda c: (max(abs(a - b) for a, b in zip(c, med)), -r.per_cell[c]["robust_score"], c)))
    obj = {c: (r.per_cell[c]["robust_score"], f[c], -dd[c]) for c in reps}
    left, out = set(reps), []
    while left and len(out) < K:
        front = [c for c in left if not any(dominates(obj[o], obj[c]) for o in left if o != c)]
        front.sort(key=lambda c: (-obj[c][0], c))
        out += front[:K - len(out)]
        left -= set(front)
    return out, r

def run(n, plateau, spikes):
    rng = np.random.default_rng(20261011)
    c = dict(rp_plat=0, rp_spike=0, naive_spike=0, hg_spike=0, hg_plat=0, rp_n=0, rp_any=0)
    for _ in range(n):
        f, plat, spk = make(rng, plateau, spikes)
        sel, r = select(f, rng)
        c["rp_plat"] += any(plat[s] for s in sel)
        c["rp_spike"] += any(spk[s] for s in sel)
        c["rp_n"] += len(sel); c["rp_any"] += bool(sel)
        c["naive_spike"] += bool(spk[np.unravel_index(np.argmax(f), f.shape)])
        cand = [cc for cc, v in r.per_cell.items() if f[cc] > THR and v["robust_score"] is not None]
        top = sorted(cand, key=lambda cc: (-r.per_cell[cc]["robust_score"], cc))[:K]
        c["hg_spike"] += any(spk[t] for t in top); c["hg_plat"] += any(plat[t] for t in top)
    print(f"plateau={plateau} spikes={spikes}:", {k: (v / n if k != 'rp_n' else v / n) for k, v in c.items()})

n = int(sys.argv[1])
run(n, True, 3)
run(n, False, 3)
run(n, False, 0)
