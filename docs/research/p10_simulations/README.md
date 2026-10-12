# P10 計画の根拠にした試算（2026-10-12）

`docs/plans/P10_PLAN.md` §5 の数字の出どころ。シード 20261012。受け入れ基準は `null_sim.py` 冒頭に**実行前に**書いた。

    uv run python docs/research/p10_simulations/null_sim.py hand
    uv run python docs/research/p10_simulations/null_sim.py run:A3:null        # A1 / A2 / A3、null（400 回）/ edge（200 回）

- `null_sim_run1_INVALID_2026-10-12.txt`: 1 回目。A2・A3 で S1（エッジなしで総損益の平均 ≈ 0）が**不合格**。原因は、足の高値・安値を独立に作り「不利な側が先」と仮定した足内の順序で、逆張りのナンピンに足ごとの「押し目買い→戻り売り」をただで与えていた。**判断には使わない**。
- `null_sim_run2_output_2026-10-12.txt`: 2 回目（有効）。H4 足を 8 ステップの連続した経路にし、注文は水準を越えたステップの価格で約定（足内の順序の仮定なし、算術価格なので過去だけを使うルールの総損益の期待値は 0）。
- 手計算のケース: 1 回目の想定値のうち 3 つは私の計算違い（0.01 ロットの 1 pip は 0.10 USD）。経緯はスクリプトの docstring と git の履歴に記録。
