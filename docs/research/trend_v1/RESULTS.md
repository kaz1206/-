# トレンドフォロー研究 v1 — 結果の記録

条件は `PREREGISTRATION.md`（実行前に記録）。この台帳には、実行したコマンド・表示された ID・結果を、段階ごとに追記する。
ログの本文（口座番号・IP を含む）は貼らない。

## 進み具合

| 段階 | 状態 | ID | 結果の要約 |
|---|---|---|---|
| 実行前の確認（設定の読込・Holdout Guard・模擬端末での通し確認） | 完了（Linux、2026-10-12） | — | 3 期間すべて Guard OK。模擬の数字は研究結果ではない |
| P1 単一テスト・P2 指標 | 完了（実機、2026-10-12） | `rn_62192f6024a089e2` / `jb_20261012063000914_4083e82a` | SUCCEEDED、照合 MATCH 15・NOT_COMPARABLE 1（日次 Sharpe、MT5 の定義が不明なため比較しない既知の項目） |
| P3 最適化 | 完了（実機、2026-10-12） | `or_667b73e2bf2d8a70` | 25/25 パス、1 ジョブ。試行の合計 1,069（1,044 ＋ 25、予想どおり）。remote/cloud 0 は `optimize show` で確認予定 |
| P4 足切り | 未実行 | | |
| P5 ロバストネス | 未実行 | | |
| P6 統計評価 | 未実行 | | |
| P7 選抜 | 未実行 | | |
| P8a 複数期間検証 | 未実行（P7 で選ばれた場合のみ） | | |
| P8b OOS | 実行しない（P8a で PASS が出たら相談） | | |
| P9 リスク | 未実行（P8a で PASS の場合のみ） | | |
| P10 帰無仮説 | A1 型の判定は済み（0.30%、上側限界 0.77%）。再実行しない | | |

## 実行の記録

### 第 1 回（2026-10-12、Windows、build 6256）

| コマンド | 主な表示 |
|---|---|
| `rlab backtest run configs\requests\trend_ma_h4_v1_single.yaml` | `status: SUCCEEDED`、`run_id: rn_62192f6024a089e2`、`strategy_version_id: sv_787bc4b5e3d3a4bb`、`candidate_id: cd_42bcdf0b32cdb210`、deals 107、net_profit_mt5 −285.08 = net_profit_sum −285.08、warnings なし、9.09 秒、`metric_checks: MATCH 15, NOT_COMPARABLE 1` |
| `rlab metrics compute --all` | 7 ジョブとも `already computed (m3)`（今回のジョブも `backtest run` の中で計算済みだったため。手順書の「computed と出る」という予想は誤りで、動作は正常） |
| `rlab optimize run configs\studies\trend_ma_h4_v1.yaml` | `status: SUCCEEDED`、`round_id: or_667b73e2bf2d8a70`、expected 25、`passes=25/25`、12.2 秒 |
| `rlab trials show RL_SmokeTest` | 過去の 580 ＋ 464 ＋ 今回の 25 = `total trials (raw N): 1069` |

読み方:
- P1 は「グリッドの中心（短期 15・長期 80）の 1 候補」を Discovery 期間で 1 回試しただけで、判定ではない。2 年間で −285.08 USD（0.10 ロット）。自前の計算と MT5 の値は 15 項目で一致し、記録の仕組みが正しく動いた。
- 履歴不足の警告（`DATA_COVERAGE`）は出ていない → 2014〜2015 年の 1 分足は揃っている。
- OHLC_M1 での最適化が実機で初めて成功した（事前登録の既知の限界 2 のうち「最適化は未確認」が解消）。
