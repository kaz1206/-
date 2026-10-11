# P9 計画の根拠にした試算（2026-10-11）

`docs/plans/P9_PLAN.md` §6 の数字の出どころ。シード 20261011。受け入れ基準は `risk_sim.py` 冒頭に**実行前に**書いた（R1c だけは R1b の初回実行後に追加し、追加である旨をスクリプトと計画書に明記）。

    uv run python docs/research/p9_simulations/risk_sim.py r1
    uv run python docs/research/p9_simulations/risk_sim.py r2:D1      # D1〜D7 を個別に（並列実行用）。P9_REPS で回数を変えられる（既定 200）

出力: `risk_sim_output_2026-10-11.txt`（R1 合格。R2 は D1〜D4・D6 合格、D5「将来のボラ 1.5 倍」未達 73% < 90%、D7 は記録のみ）。
試作（numpy・scipy・arch のみ、robustlab を使わない）。実装後に本番の関数で同じ条件・同じシードで再検証する。
