# トレンドフォロー研究 v1 — 結果の記録

条件は `PREREGISTRATION.md`（実行前に記録）。この台帳には、実行したコマンド・表示された ID・結果を、段階ごとに追記する。
ログの本文（口座番号・IP を含む）は貼らない。

## 進み具合

| 段階 | 状態 | ID | 結果の要約 |
|---|---|---|---|
| 実行前の確認（設定の読込・Holdout Guard・模擬端末での通し確認） | 完了（Linux、2026-10-12） | — | 3 期間すべて Guard OK。模擬の数字は研究結果ではない |
| P1 単一テスト・P2 指標 | 完了（実機、2026-10-12） | `rn_62192f6024a089e2` / `jb_20261012063000914_4083e82a` | SUCCEEDED、照合 MATCH 15・NOT_COMPARABLE 1（日次 Sharpe、MT5 の定義が不明なため比較しない既知の項目） |
| P3 最適化 | 完了（実機、2026-10-12） | `or_667b73e2bf2d8a70` | 25/25 パス、1 ジョブ。試行の合計 1,069（1,044 ＋ 25、予想どおり）。`optimize show` で remote 0・cloud 0・local 25 を確認 |
| P4 足切り | 完了（実機） | `fl_858a5b8e4f839061` | **合格 0 / 25**。LOW_PSR 25、NEGATIVE_EXPECTANCY 11（警告 TRADE_FREQUENCY_OUT_OF_PROFILE 4） |
| P5 ロバストネス | 完了（実機） | `rb_c8f4beef7b086932` | 島 0（NO_FILTER_PASS）。Robust Score の最大 0.0096、中央値 −0.0057 |
| P6 統計評価 | 完了（実機） | `st_663d61458fc3e061` | N = 1,069、SR₀ = 0.0763（日次）、保守的な DSR の最大 0.242、0.95 以上 0 件。ブートストラップ対象なし |
| P7 選抜 | 完了（実機） | `sl_12219cb0ae074eb7` | **選抜 0 件**（`NO_ELIGIBLE: no candidate passed P4`） |
| P8a 複数期間検証 | **実行しない**（P7 が 0 件のため。事前登録どおり診断も行わず、V0・V1 は未使用のまま） | | |
| P8b OOS | 実行しない（P8a で PASS が出たら相談） | | |
| P9 リスク | **実行しない**（P8a の PASS がないため。ロットは出さない） | | |
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

### 第 2 回（2026-10-12、Windows）

| コマンド | 主な表示 |
|---|---|
| `rlab optimize show or_667b73e2bf2d8a70` | passes_stored=25、chunks_succeeded=1/1、trials_in_family 1069、純損益 最小 −1402.25・中央値 62.06・最大 1550.07、黒字の割合 0.56、agents: local 25・remote 0・cloud 0 |
| `rlab metrics show rn_62192f6024a089e2` | 53 取引、勝率 26.4%、PF 0.92、残高の最大 DD 1401.24（12.6%）、エクイティの最大 DD 1764.14（15.4%）、日次 Sharpe −0.0080、518 日。年別: 2014 年 +50.96、2015 年 −336.04 |
| `rlab filter run or_667b73e2bf2d8a70 --profile swing_trend` | `fl_858a5b8e4f839061`、合格 0・不合格 25、LOW_PSR 25、NEGATIVE_EXPECTANCY 11、警告 TRADE_FREQUENCY_OUT_OF_PROFILE 4、server MetaQuotes-Demo |
| `rlab robust run or_667b73e2bf2d8a70 --filter fl_858a5b8e4f839061` | `rb_c8f4beef7b086932`、score_status OK 25、Robust Score 最小 −0.0152・中央値 −0.0057・最大 0.0096、軸の感度 Fast 0.0124・Slow 0.0152、端 4、島 0（NO_FILTER_PASS）、黒字年の割合 0.0: 3・0.5: 20・1.0: 2 |
| `rlab robust map … --metric score` / `--metric robust` | スコアの上位は長期 40 の行（グリッドの端）に集中 |
| `rlab stats run … --seed 20261015` | `st_663d61458fc3e061`、N 1069、V[SR] 0.000543（1,014 件）、SR₀ 0.0763（ラウンド内だけなら 0.0328）、N_eff 1.84（参考）、DSR の最大 0.242・中央値 0.045・0.95 以上 0、SERIAL_DEPENDENCE_HIGH 0、ブートストラップ対象なし |
| `rlab select run … --stats st_663d61458fc3e061` | `sl_12219cb0ae074eb7`、`NO_ELIGIBLE: no candidate passed P4`、選抜 0 |

## 結論（v1）

- **25 候補すべてが P4 で不合格（LOW_PSR）。P7 の選抜は 0 件。** 事前登録の「止まる条件」に従い、ここで終了した。基準は変えていない。
- 意味: 「EURUSD・H4・2014〜2015 年の移動平均交差では、偶然と区別できるだけの証拠がない」。**優位性がないことの証明ではない**（証拠不足）。
- P6 の参考値: 1,069 回試したとき、まぐれでも最も良い候補が届きうる日次 Sharpe の目安（SR₀）は 0.076。今回の候補の DSR は最大でも 0.24 で、この目安を超えたと言える候補はなかった。
- 観察（判定ではない）: スコアの上位は長期 40 の行に集まっていて、グリッドの端だった。端の外側は今回のグリッドでは調べていない。これを見てから範囲を広げると「結果を見た後の探索」になるので、行うなら新しい版として事前登録し、試行回数に数える。
- 基盤の動作: P1〜P7 はすべて正常に完了した（照合の一致、25/25 パス、remote/cloud 0、各段階 DONE、合格 0 件のときの「選抜 0 件・理由つき」）。
- 守ったこと: Holdout には触れていない。V0・V1 は見ていない（この家族の将来の検証に使える）。P9 のロットは出していない。
- P10（A1 型、0.30%）は「判定の仕組みが偶然を合格させにくい」ことの確認で、この EA の収益性とは関係しない。今回は合格者がいないので、P10 の数字が関わる場面はなかった。

## P1〜P10 の完了状況（v1）

| 段階 | 状況 |
|---|---|
| P1・P2 | 完了 |
| P3 | 完了 |
| P4 | 完了（合格 0） |
| P5 | 完了（島 0） |
| P6 | 完了 |
| P7 | 完了（選抜 0） |
| P8a | 対象なしのため実行せず |
| P8b | 対象なしのため実行せず（Holdout は封印のまま） |
| P9 | 対象なしのため実行せず |
| P10 | A1 型の判定は以前に完了。今回は再実行していない |
