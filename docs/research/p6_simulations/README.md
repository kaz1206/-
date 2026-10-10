# P6 計画の根拠にしたシミュレーション（2026-10-11）

`docs/plans/P6_PLAN.md` §2・§6 の数字の出どころ。シードは 20261011。実行例（リポジトリのルートで）:

    uv run python docs/research/p6_simulations/dsr_mc.py 2000     # 相関の強さと PSR/DSR の偶然の合格率
    uv run python docs/research/p6_simulations/dsr_mc2.py 2000    # 群に分かれた相関・自己相関・ボラティリティの局面
    uv run python docs/research/p6_simulations/dsr_mc3.py 2000    # 自己相関の補正（実効標本数）の効果と検出力
    uv run python docs/research/p6_simulations/cov.py 400 499     # ブートストラップ区間のカバレッジ（arch が必要）

本体（`src/`）からは参照しない。実装では同じ実験を、合格基準つきのテストとして書き直す。
