# P7 計画の根拠にした試算（2026-10-11）

`docs/plans/P7_PLAN.md` §6.1 の「実装前の試算」の出どころ。シード 20261011。実行（リポジトリのルートで、約 3 分）:

    uv run python docs/research/p7_simulations/proto.py 500

出力（2026-10-11）:

    plateau=True spikes=3:  rp_plat 1.0, rp_spike 0.0, naive_spike 0.998, hg_spike 0.0, hg_plat 0.996, rp_n 2.08, rp_any 1.0
    plateau=False spikes=3: rp_plat 0.0, rp_spike 0.0, naive_spike 1.0, hg_spike 0.45, rp_n 1.122, rp_any 0.73
    plateau=False spikes=0: rp_spike 0.0, naive_spike 0.0, hg_spike 0.0, rp_n 1.092, rp_any 0.728

（rp = 領域の代表＋パレート、naive = 生のスコア最大の 1 点、hg = P4 合格を Robust Score 順に上位 5）

P4 の合格・DSR・最大 DD は簡易な代用。本体（`src/`）からは参照しない。実装では本番の関数で同じ実験を合格基準つきのテストとして書き直す。
