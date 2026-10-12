"""One P10 round through the PRODUCTION decision functions, and a whole execution (P10_PLAN §1.3, §2).

Discovery -> P4 filtering.gates.evaluate -> P5 robustness.grid_stats.compute -> P7 selection.core.decide (budget 5)
-> P8a stats.validation.evidence + holm, ruin / profit / extra-cost gates on V0 + V1 -> passers run one more year.
Stand-ins (recorded): DSR rank = Discovery daily Sharpe, resampled DD = observed equity max DD %, the
reproducibility gate always passes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import beta, kurtosis, norm, skew

from robustlab.config.loader import load_profile
from robustlab.filtering import gates as p4
from robustlab.metrics.engine import daily_metrics
from robustlab.nulltest import sim
from robustlab.robustness import grid_stats
from robustlab.selection import core
from robustlab.stats import validation as ev

PIPELINE_VERSION = "pipe1"
BUDGET, PEAK_RATIO_MAX, ROBUST_MIN = 5, 1.5, 0.0
GRID_SHAPE = (5, 5)


@dataclass(frozen=True)
class Setup:
    acc: sim.Account
    pm: sim.PriceModel
    days: dict[str, int]
    chunk: int
    profiles_path: Path


def setup_of(cfg: dict[str, Any], profiles_path: Path) -> Setup:
    return Setup(sim.Account(**cfg["account"]), sim.PriceModel(**cfg["price"]), dict(cfg["days"]), int(cfg["chunk"]),
                 profiles_path)


def lane_metrics(bk: sim.Book, i: int, deposit: float) -> tuple[dict[str, Any], np.ndarray]:
    eq = np.array([e[i] for e in bk.eq_close])
    mn = np.array([e[i] for e in bk.eq_min])
    rows = [{"date": f"d{k}", "equity_close": f"{v:.2f}"} for k, v in enumerate(eq)]
    m: dict[str, Any] = {}
    na: dict[str, str] = {}
    daily_metrics(rows, deposit, m, na)
    peak = np.maximum.accumulate(np.concatenate([[deposit], eq[:-1]]))
    m.update({"bankrupt": bool(mn.min() <= 0), "stop_out_deals": int(bk.stop_outs[i]),
              "min_margin_level": None if not math.isfinite(bk.minml[i]) else float(bk.minml[i]),
              "max_positions": int(bk.maxpos[i]), "trades": int(bk.trades[i]), "net_profit": float(eq[-1] - deposit),
              "equity_max_dd_pct": float(((peak - mn) / peak).max() * 100)})
    return m, np.diff(np.concatenate([[deposit], eq])) / deposit


def _key(i: int, j: int) -> str:
    return f"cd_{i}_{j}"


def evaluate_round(spec: dict[str, Any], st: Setup, books: dict[str, sim.Book], r: int, P: int,
                   rng: np.random.Generator) -> dict[str, Any]:
    acc = st.acc
    prof, _ = load_profile(st.profiles_path, spec["profile"])
    scores = np.full(GRID_SHAPE, np.nan)
    statuses = np.full(GRID_SHAPE, "OK", dtype=object)
    passed = np.zeros(GRID_SHAPE, bool)
    mets: dict[str, tuple[dict[str, Any], int]] = {}
    D = books["D"]
    for p in range(P):
        i = r * P + p
        m, _ = lane_metrics(D, i, acc.deposit)
        idx = np.unravel_index(p, GRID_SHAPE)
        s, status, _ = grid_stats.score_of(m)
        scores[idx], statuses[idx] = s, status
        passed[idx] = p4.evaluate(m, prof, st.days["D"] / 260).outcome == "PASS"
        mets[_key(*idx)] = (m, i)
    res = grid_stats.compute(scores, statuses.astype(str), passed, list(spec["axes"]))
    cells = [tuple(int(x) for x in c) for c in np.argwhere(passed)]
    out: dict[str, Any] = {"p4": len(cells), "selected": 0, "passers": []}
    if not cells:
        return out
    index_of = {_key(*c): c for c in cells}
    regions = [([_key(*cc) for cc in rg["members"]], _key(*rg["medoid"])) for rg in res.regions]
    for members, med in regions:
        for x in members + [med]:
            index_of[x] = tuple(int(v) for v in x[3:].split("_"))
    extra = {_key(*c): (mets[_key(*c)][0].get("daily_sharpe"), mets[_key(*c)][0]["equity_max_dd_pct"]) for c in cells}
    dec = core.decide(scores, statuses.astype(str), [_key(*c) for c in cells], index_of,
                      {_key(*c): res.per_cell[c]["region_status"] for c in cells}, regions, extra, BUDGET,
                      PEAK_RATIO_MAX, ROBUST_MIN)
    sel = dec.selection.selected
    out["selected"] = len(sel)
    if not sel:
        return out
    pv: dict[str, float] = {}
    info: dict[str, dict[str, Any]] = {}
    V0, V1, F = books["V0"], books["V1"], books["F"]
    for cd in sel:
        i = mets[cd][1]
        m0, r0 = lane_metrics(V0, i, acc.deposit)
        m1, r1 = lane_metrics(V1, i, acc.deposit)
        x = np.concatenate([r0, r1])
        sd = x.std(ddof=1)
        lb = 1.0  # lower bound of the P8a p-value (1 - standard PSR); above alpha nothing can make it pass
        if sd > 0:
            sr, g3, g4 = x.mean() / sd, skew(x), kurtosis(x, fisher=False)
            v = 1 - g3 * sr + (g4 - 1) / 4 * sr * sr
            if v > 0:
                lb = 1 - norm.cdf(sr * math.sqrt(len(x) - 1) / math.sqrt(v))
        pv[cd] = ev.evidence([r0, r1], 2000, rng)["p"] if lb <= ev.ALPHA else lb
        net = m0["net_profit"] + m1["net_profit"]
        lots = (V0.cost[i] + V1.cost[i]) / (acc.spread * acc.contract)
        info[cd] = {"ruin": m0["bankrupt"] or m1["bankrupt"] or m0["stop_out_deals"] > 0 or m1["stop_out_deals"] > 0,
                    "net": net, "net_cost": net - acc.pip * acc.contract * lots, "lane": i}
    rej = ev.holm(pv)
    for cd in sel:
        x = info[cd]
        if rej[cd] and not x["ruin"] and x["net"] > 0 and x["net_cost"] > 0:
            i = x["lane"]
            out["passers"].append({"cd": cd, "p": pv[cd],
                                   "fut_ruin": bool(F.stop_outs[i] > 0 or min(e[i] for e in F.eq_min) <= 0),
                                   "fut_net": float(F.eq_close[-1][i] - acc.deposit)})
    return out


def structure_check(books: dict[str, sim.Book], days: dict[str, int], deposit: float,
                    rng: np.random.Generator) -> dict[str, Any]:
    """S3: stop-out in the next year, EA re-run vs the lane's own closed P&L shuffled (martingale, first chunk)."""
    D, V0, V1, F = (books[k] for k in ("D", "V0", "V1", "F"))
    surv = [i for i in range(len(D.bal)) if D.stop_outs[i] == 0 and V0.stop_outs[i] == 0 and V1.stop_outs[i] == 0
            and len(D.closed[i]) + len(V0.closed[i]) + len(V1.closed[i]) >= 30]
    struct = float(np.mean([F.stop_outs[i] > 0 for i in surv])) if surv else float("nan")
    naive = []
    years = sum(days[k] for k in ("D", "V0", "V1")) / 260
    for i in surv:
        pnl = np.array(D.closed[i] + V0.closed[i] + V1.closed[i])
        n = max(1, int(round(len(pnl) / years)))
        ruins = 0
        for _ in range(200):
            path = deposit + np.cumsum(rng.choice(pnl, n, replace=True))
            ruins += path.min() <= 0.5 * deposit
        naive.append(ruins / 200)
    return {"lanes": len(surv), "of": len(D.bal), "structural": struct,
            "naive_shuffle": float(np.mean(naive)) if naive else float("nan")}


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> dict[str, float]:
    a = (1 - conf) / 2
    return {"lower": float(beta.ppf(a, k, n - k + 1)) if k else 0.0,
            "upper": float(beta.ppf(1 - a, k + 1, n - k)) if k < n else 1.0,
            "upper_one_sided": float(beta.ppf(conf, k + 1, n - k)) if k < n else 1.0}


def execute(name: str, spec: dict[str, Any], st: Setup, kind: str, rounds: int, seed: int, arch_index: int,
            evidence_seeding: str = "global", progress=None) -> dict[str, Any]:
    """A whole execution. evidence_seeding 'prototype' reproduces the pre-implementation script (one bootstrap stream
    per round index inside a chunk); 'global' (production) uses one stream per round."""
    ar = 0.0 if kind == "null" else float(spec["edge_ar"])
    rng = np.random.default_rng([seed, arch_index, 0 if kind == "null" else 1])
    P = len(spec["a"]) * len(spec["b"])
    rows, gross = [], []
    s3 = None
    for start in range(0, rounds, st.chunk):
        R = min(st.chunk, rounds - start)
        books = {k: sim.simulate(spec, sim.prices(R, st.days[k], rng, st.pm, st.acc.start_price, ar), st.acc, st.pm)
                 for k in ("D", "V0", "V1", "F")}
        D = books["D"]
        for r in range(R):
            lanes = slice(r * P, (r + 1) * P)
            gross.append(float(np.mean(D.eq_close[-1][lanes] - st.acc.deposit + D.cost[lanes])))
            ev_rng = np.random.default_rng([seed, 7, r] if evidence_seeding == "prototype" else [seed, 7, start + r])
            rows.append(evaluate_round(spec, st, books, r, P, ev_rng))
        if spec["type"] == "martingale" and kind == "null" and start == 0:
            s3 = structure_check(books, st.days, st.acc.deposit, np.random.default_rng([seed, 33]))
        if progress:
            progress(start + R, rounds)
    return summarize_rows(name, kind, rounds, seed, rows, gross, s3)


def summarize_rows(name: str, kind: str, rounds: int, seed: int, rows: list[dict[str, Any]], gross: list[float],
                   s3: dict[str, Any] | None) -> dict[str, Any]:
    k = sum(1 for x in rows if x["passers"])
    g = np.array(gross)
    se = float(g.std(ddof=1) / math.sqrt(len(g))) if len(g) > 1 else float("nan")
    ps = [p for x in rows for p in x["passers"]]
    return {"archetype": name, "kind": kind, "rounds": rounds, "seed": seed, "pass_rounds": k,
            "pass_rate": k / rounds, "ci": clopper_pearson(k, rounds),
            "p4_mean": float(np.mean([x["p4"] for x in rows])), "selected_mean": float(np.mean([x["selected"]
                                                                                                for x in rows])),
            "passers": len(ps), "passers_ruined_next_year": sum(p["fut_ruin"] for p in ps),
            "passers_next_year_net_mean": float(np.mean([p["fut_net"] for p in ps])) if ps else None,
            "passers_ruined_ci": clopper_pearson(sum(p["fut_ruin"] for p in ps), len(ps)) if ps else None,
            "s1": {"gross_mean": float(g.mean()), "se": se, "z": float(g.mean() / se) if se and se > 0 else None},
            "s3": s3}
