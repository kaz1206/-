# トレンドフォロー研究 v2 — 結果の記録

条件は `PREREGISTRATION.md`（実行前に記録）。ログの本文（口座番号・IP を含む）は貼らない。

## 進み具合

| 段階 | 状態 | ID | 結果の要約 |
|---|---|---|---|
| 実行前の確認（設定の読込・Holdout Guard） | 完了（Linux、2026-10-12） | — | 3 期間すべて OK |
| P1 単一テスト・P2 指標 | 完了（実機） | `rn_b72b7b4c0301bdec` | SUCCEEDED、warnings なし（2008〜2013 年の履歴あり）、照合 MATCH 15・NOT_COMPARABLE 1 |
| P3 最適化 | 完了（実機） | `or_7a7abd9dd33fc6d4` | 25/25、local 25・remote 0・cloud 0、試行の合計 1,094 |
| P4 足切り | 完了（実機） | `fl_ca1599e8c4b372a8` | **合格 0 / 25**。LOW_PSR 25、NEGATIVE_EXPECTANCY 2 |
| P5 ロバストネス | 完了（実機） | `rb_9ab1542fa4272c4d` | 島 0（NO_FILTER_PASS）。Robust Score 中央値 0.0153・最大 0.0231（v1 は中央値 −0.0057） |
| P6 統計評価 | 完了（実機） | `st_930fb7435e4c5227` | SR₀ 0.0767、保守的な DSR の最大 0.041 |
| P7 選抜 | 完了（実機） | `sl_2cbf59de913b7335` | **選抜 0 件**（NO_ELIGIBLE） |
| P8a 複数期間検証 | **実行しない**（選抜 0。V0・V1 は未見のまま） | | |
| P8b OOS | 実行しない（P8a で PASS が出たら相談） | | |
| P9 リスク | **実行しない**（ロットは出さない） | | |
| P10 | A1 型の判定は済み（2 年の Discovery で測定、v2 では目安）。再実行しない | | |

## 実行の記録

### 実行（2026-10-12、Windows、build 6256）

| コマンド | 主な表示 |
|---|---|
| `rlab backtest run configs\requests\trend_ma_h4_v2_single.yaml` | `rn_b72b7b4c0301bdec`、SUCCEEDED、deals 313、純損益 3,455.92（MT5 と一致）、warnings なし、41.2 秒 |
| `rlab metrics show rn_b72b7b4c0301bdec` | 156 取引、勝率 34.0%、PF 1.27、残高の最大 DD 1,763.46（13.9%、597 日）、エクイティの最大 DD 2,308.28（17.5%）、最大連敗 11、日次 Sharpe 0.0231、1,559 日。年別: 2008 +981、2009 −21、2010 +1,947、2011 −348、2012 +1,049、2013 −152 |
| `rlab optimize run configs\studies\trend_ma_h4_v2.yaml` | `or_7a7abd9dd33fc6d4`、25/25、20.2 秒 |
| `rlab optimize show or_7a7abd9dd33fc6d4` | trials_in_family 1094、純損益 最小 −2,928.8・中央値 3,328.87・最大 4,897.29、黒字の割合 0.92、local 25・remote 0・cloud 0 |
| `rlab filter run or_7a7abd9dd33fc6d4 --profile swing_trend` | `fl_ca1599e8c4b372a8`、合格 0・不合格 25、LOW_PSR 25、NEGATIVE_EXPECTANCY 2、警告 TRADE_FREQUENCY_OUT_OF_PROFILE 4 |
| `rlab robust run … --filter fl_ca1599e8c4b372a8` | `rb_9ab1542fa4272c4d`、Robust Score 最小 −0.0189・p25 0.0101・中央値 0.0153・p75 0.0220・最大 0.0231、peak_gap 中央値 −0.0004、軸の感度 Fast 0.0075・Slow 0.0057（v1 より小さい）、端 4、島 0、黒字年の割合の分布 1/6: 1・2/6: 2・3/6: 7・4/6: 9・5/6: 6 |
| `rlab robust map … --metric robust` | 長期 100〜120 × 短期 10〜20 に高い値のなだらかな台地 |
| `rlab stats run … --seed 20261016` | `st_930fb7435e4c5227`、N 1069（異なる設定の数。v2 の 25 候補は v1 と同じパラメータ・銘柄・時間足なので数が増えない）、試行台帳の合計 1094、SR₀ 0.0767、N_eff 1.82、DSR の最大 0.041 |
| `rlab select run …` | `sl_2cbf59de913b7335`、`NO_ELIGIBLE: no candidate passed P4`、選抜 0 |

## 結論（v2）

- **25 候補すべてが P4 で不合格（LOW_PSR）。選抜 0 件。** 事前登録どおりここで終了。基準は変えていない。
- v1 との違い: 地形はプラス側に動いた（黒字 92%、Robust Score の中央値 +0.0153、長期 100〜120 になだらかな台地）。ただし 1 候補ごとの成績は日次 Sharpe 0.02〜0.03 程度（年率 約 0.3〜0.5）で、6 年でも P4 の目安（日次 約 0.042）に届かない。
- 意味: 「台地らしい形はあるが、偶然と区別できるだけの証拠はない」。**優位性がない証明ではない。**
- 注意: 「92% が黒字」は強い証拠ではない。25 候補は似た動きをしていて、実質的な独立数は約 1.8（N_eff）。
- 年別の成績はトレンドの年（2008・2010・2012）に勝ち、それ以外で小さく負ける典型的な形。v1 の期間（2014〜2015）はマイナスだった → 期間によって成績が大きく変わる。
- 守ったこと: Holdout・V0・V1 は未見、ロットは出していない。

