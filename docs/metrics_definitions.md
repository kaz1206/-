# 指標定義（MetricEngine）

実装: `src/robustlab/metrics/engine.py`（純粋関数）、照合: `src/robustlab/metrics/compare.py`。
定義を変えたら `METRIC_DEF_VERSION` を上げ、本書に新しい版の節を追加する（古い計算結果は消さない）。

## 統計評価（P6、dsr1 ＋ bs1、C61〜C67）

**すべて `DISCOVERY_IN_SAMPLE`（候補を選んだ期間の内側の値）。OOS の証拠ではない。DSR は警告・順位づけのみに使い、単独で合否を決めない（C62）。**

| 値 | 定義 |
|---|---|
| 入力のリターン | 日次 Parquet の日末エクイティから r_t = (E_t − E_{t−1}) / D、E_0 = D（C53。保存済みの `daily_sharpe` と一致することを毎回確認） |
| N（ファミリー） | 同じ `strategy_family` の全ラウンドで、依頼したパラメータ空間を全展開した `candidate_id` の異なる数（失敗したパスも含む。再実行・重なるラウンドを二重に数えない） |
| V[SR]（ファミリー） | 同じファミリーの成功したジョブの結果のうち、異なる（`candidate_id`・期間）の組で `score_status = OK` のものの `daily_sharpe` の標本分散（ddof = 1） |
| 除外 | `BANKRUPTCY`・`STOP_OUT`・`NO_TRADES`・`NOT_COMPUTABLE` は Sharpe・DSR・区間を null と理由にし、V に入れず N には数える。件数を状態別に記録 |
| SR₀ | √V · ((1 − γ)Φ⁻¹(1 − 1/N) + γΦ⁻¹(1 − 1/(N·e)))。N = 1 または V = 0 なら 0（理由を記録） |
| `dsr` | PSR(SR₀)（P4 と同じ PSR の式、尖度の項あり） |
| `dsr_ar1` | n を n* = n(1 − ρ̂₁)/(1 + ρ̂₁) に置き換えた PSR(SR₀)（ρ̂₁ > 0 のときのみ） |
| `dsr_newey_west` | n を n / f に置き換えた PSR(SR₀)。f = 1 + 2Σ_{k≤L}(1 − k/(L+1))ρ̂_k、L = ⌊4(n/100)^{2/9}⌋（f > 1 のときのみ） |
| `dsr_bootstrap_se` | Φ((SR − SR₀) / SE)。SE は定常ブートストラップの再標本 Sharpe の標準偏差（ブートストラップ対象の候補のみ） |
| `dsr_conservative` | 計算できた上の値の最小値（使った値の名前を `dsr_conservative_components` に記録）（**独自拡張**） |
| `dependence` | 自己相関 1〜5 次（標本平均を引いた系列の比）、n*、L、f（長期分散の比）。n/n* ≥ 1.5 で `SERIAL_DEPENDENCE_HIGH`（**独自拡張**の目安） |
| `dsr_round_scope` | N・V をそのラウンドだけで計算した標準 DSR（感度。判定に使わない） |
| N_eff（参考） | ラウンド内の日次リターンの相関行列の参加比 (Σλ)²/Σλ²。SR₀ には使わない（ONC は P12） |
| ブートストラップ | 定常（arch `StationaryBootstrap`、平均ブロック長 = `optimal_block_length` の `stationary` 列、1 未満は 1）。感度として循環ブロック（`circular` 列を四捨五入）。B 既定 2,000、候補ごとのシード = sha256(実行シード｜candidate_id) の先頭 8 桁 |
| 期待値・Sharpe の区間 | 再標本の値のパーセンタイル（5%・95%、線形補間）。期待値は比率と × D の金額。再標本で分散 0 が 1% を超えたら Sharpe 区間は null（不安定） |
| 再標本化した最大 DD の分布 | 再標本ごとに E*_t = D + D·Σr*、最高値は D を含めて累積最大、DD = 最高値 − E*。最大 DD（口座通貨）とその時点の %、の 5・50・95% 点。E* ≤ 0 になった再標本の割合も記録。**日末エクイティの DD なので、ティック単位の `equity_max_dd` とは別物** |
| 区間を出さない場合 | 30 日未満、NaN・無限大を含む、分散 0（理由を記録） |
| 警告コード | `LOW_DSR`（保守的な DSR < 0.95）、`SERIAL_DEPENDENCE_HIGH`、`BOOTSTRAP_SENSITIVITY`（循環ブロックの区間幅が定常の 2/3〜3/2 倍の外）。判定ではない |

注意:
- 巨大な利益の日が 1 日あると、歪度の項（−γ₃·SR）で PSR/DSR が**上がる**（式の性質）。巨大な損失の日は下がる。外れ値の影響は Sharpe の区間の広さで確認する
- 既知の限界（AR(2) 型の依存では、どの DSR も偶然の合格率 7% 以下を満たさない など）は `docs/research/p6_validation/RESULTS.md` と、各実行の `known_limitations_json` に記録

## ロバストネス指標（P5、g1、C56〜C59）

実装: `src/robustlab/robustness/grid_stats.py`（純粋関数）、`subperiod.py`。全候補（P4 の合否によらない）について計算し、`evaluation`（stage = `ROBUSTNESS`）に保存する。

| 指標 | 定義 |
|---|---|
| スコア s(c) | 日次シャープ（p1 の `daily_sharpe`）。使えないときは計算の内部で −∞ とし、原因を `score_status` に記録（優先順: BANKRUPTCY > STOP_OUT > NO_TRADES > NOT_COMPUTABLE、該当する全部を `score_flags` に） |
| 近傍 N(c) | グリッド上のチェビシェフ距離 1（2 軸で最大 8 点）。グリッドの外は数えない。`neighbours` に点数、内側の半分未満なら `edge` |
| Robust Score | {s(c)} ∪ {s(n)} の 25% 分位点（`inverted_cdf`）。−∞ を含めて計算し、結果が −∞ なら null と理由 |
| neighbour_median / Peak Gap | 近傍（自身を除く）の中央値（`inverted_cdf`）、s(c) − 中央値 |
| Peak Ratio | s(c) ÷ 近傍の中央値。中央値 ≤ 0 なら計算しない（**独自拡張**、C56） |
| neighbour_status_counts | 近傍の点の `score_status` ごとの件数 |
| neighbour_pass_rate | 近傍のうち P4 合格の割合 |
| 感度 | 軸方向に隣り合う 2 点のスコア差の絶対値。ラウンド全体は中央値、候補ごとは最大値（両方とも有限の組だけ） |
| 島 | P4 合格点を斜めも含めた連結で分けたもの（面積 2 以上）。メドイド = 島内の他の点とのチェビシェフ距離の合計が最小の点（同点なら Robust Score が高い方、さらに同点なら座標が小さい方） |
| region_status / regions_reason | 候補: IN_REGION / NOT_FILTER_PASS / ISOLATED_PASS。ラウンド: NO_FILTER_PASS / ALL_ISOLATED |
| サブ期間 | 暦年ごとに Σ r_t と日次シャープ（r_t は PSR の入力と同じ定義）。黒字年の割合、最悪年、年別シャープの標準偏差（暦年が 2 未満なら理由つき） |

保存では −∞・NaN を数値として残さない（null ＋ `*_reason`）。

## PSR の入力（P4、C53・C54）

P4 の PSR ゲート（`src/robustlab/stats/psr.py`、`filtering/gates.py`）は、次の値を入力にする。いずれも m3（単一テスト）・p1（最適化パス）の日次系列の指標そのもの。

- 日次リターン: **r_t = (E_t − E_{t−1}) / D**。E_t は日 t の日末エクイティ（含み損益を含む）、D は初期資金で期間中一定、E_0 = D。日はティックのあったサーバ日（土日・休場日は含まない）。ポジションがない日は r_t = 0
- ŜR = `daily_sharpe`（平均 ÷ 標本標準偏差 ddof=1、年率化なし、無リスク金利 0）、γ̂₃ = `daily_skewness`、γ̂₄ = `daily_kurtosis`（超過でない）、n = `days`
- PSR(SR*) = Φ((ŜR − SR*)·√(n−1) / √(1 − γ̂₃·ŜR + (γ̂₄−1)/4·ŜR²))、MinTRL = 1 + (1 − γ̂₃·ŜR + (γ̂₄−1)/4·ŜR²)·(z_p / (ŜR − SR*))²。R PerformanceAnalytics 2.1.0 と同じ式で、**尖度の項を常に使う**（R の既定値 `ignore_kurtosis = TRUE` とは違う）
- 無効値（日数 < 2、分散 0、NaN・無限大、分母 ≤ 0）は PSR = null とし、**不合格**
- 意味: PSR の閾値 0.95 は「真の Sharpe が SR* を上回ることを支持する」水準。**将来の利益の確率ではなく**、多数の試行から選んだことの補正（DSR、P6）も含まない
- ロットを k 倍すると全ての r_t が k 倍になるだけなので、Sharpe・PSR はロットに依存しない

## p1（2026-10-10、最適化パス用、現行）

最適化の各パス（`pass_result`）の指標。パスごとの約定一覧はないので、m2 とは入力が違う（P3_PLAN §3.4）。

| 区分 | 指標 | 出所 |
|---|---|---|
| 取引 | `net_profit`、`trades`、`win_trades`/`loss_trades`、`gross_profit`/`gross_loss`、`profit_factor`、`expected_payoff`、`win_rate`、`max_consec_wins`(+`_amount`)、`max_consec_losses`(+`_amount`)、`balance_max_dd`、`final_balance` | Frames で送った `TesterStatistics`。**P2 で約定データからの自前計算と MT5 の値が一致すると確認できた指標だけ**を使う（「研究に使う値は自前で計算」の例外。根拠 S-HW-P1、S-HW-P2）。連勝・連敗は H1（C37）の対応 |
| エクイティ | `equity_max_dd`、`equity_max_dd_pct`、`equity_peak`、`recovery_factor`、`max_floating_loss`、`max_positions`、`max_lots`、`min_margin_level`、`stop_out_deals`、`bankrupt` | EA のティック単位の追跡値（m2 と同じ定義） |
| 日次 | `days`、`daily_mean_return`、`daily_std_return`、`daily_sharpe`、`daily_skewness`、`daily_kurtosis` | 日次エクイティから自前で計算（m2 と同じコード `daily_metrics`） |

m2 にあって p1 にない指標: `inout_deals`、`distinct_volumes`、`fixed_lot`、`balance_max_dd_pct`、`balance_max_dd_days`、期間別の成績（約定一覧が必要なもの）。必要な候補は単一テストで再実行して m2 で計算する。

照合: 各パスの `net_profit` と `trades` を XML レポートの `Profit`・`Trades` と突き合わせ、1 件でも違えばジョブを SUCCEEDED にしない（C49）。

## m3（2026-10-11、単一テスト用、現行）

m2 からの変更点は 1 つだけ（L1、C50）。

| 変更 | m2 | m3 |
|---|---|---|
| 最長の連続が複数あるとき | 最後の連続を採る | **金額の絶対値が最大の連続を採る**（金額も同じなら後のもの） |

根拠（実機データ 3 つ、すべて MT5 と一致するのは m3 の規則だけ）:

| データ | 同じ長さの最長連続 | MT5 | 最初 | 最後（m2） | 金額最大（m3） |
|---|---|---|---|---|---|
| S-HW-P1（R1、1 週間） | 1 つだけ | — | ✅ | ✅ | ✅ |
| S-HW-P2（2023 年） | 連勝 4 回: 19.41 → 19.99 | 19.99 | ❌ | ✅ | ✅ |
| S-HW-P3E2E（2022〜2023 年、Fast 12 / Slow 50） | 連敗 9 回: −54.26 → −35.59 | −54.26 | ✅ | ❌ | ✅ |

最適化パスの指標（p1）は `TesterStatistics` の値をそのまま使うので、この規則の影響を受けない（E2E でも −54.26 だった）。

## m2（2026-10-10、単一テスト用）

m1 からの変更点だけを書く（それ以外は m1 と同じ）。根拠は実機データ（`tests/fixtures/hw_20261009`、`hw_20261010`）。

| 変更 | m1 | m2 |
|---|---|---|
| 最長の連続が複数あるとき（H2） | 最初の連続を採る | **最後の連続を採る**。2 つの実機データの両方で MT5 と一致（2023 年: 長さ 4 の連勝が 19.41 → 19.99 の順にあり、MT5 は 19.99）。「金額が最大の連続」でも一致するため、どちらの規則かは未確定。今後のデータで `rlab metrics runs <job_id>` で確認する |
| MT5 統計値との対応（H1） | 回数と金額を逆に対応させていた | `STAT_MAX_CONWINS` / `STAT_MAX_CONLOSSES` ＝ 最長の連勝・連敗の**金額**、`STAT_MAX_CONPROFIT_TRADES` / `STAT_MAX_CONLOSS_TRADES` ＝ その**回数**（実機の値で確認） |
| エクイティ DD の照合（H3） | 一致（差 0.01 以内）を要求 | 研究に使う値は EA がティックごとに追跡した値（保守的）。MT5 との相対差が 1% 以内なら `APPROX_MATCH`、超えたら `MISMATCH`。実測の差は 0.26%（85.33 対 85.11）。MT5 の評価タイミングは公式資料で確認できない |

照合状態に `APPROX_MATCH` を追加。

## m1（2026-10-10）

### 入力

| 入力 | 出所 | 備考 |
|---|---|---|
| 約定一覧 | Telemetry `deals.csv`（全約定。magic で絞らない） | `time_msc`、`ticket` の順に並べて使う |
| 初期資金 | Telemetry `env.json` の `initial_balance` | |
| EA 追跡値 | Telemetry v2 の `env.json`（`equity_peak`、`equity_max_dd` など） | v1 にはない → 該当指標は理由付き null |
| 日次系列 | Telemetry v2 の `daily.csv` | v1 にはない → 該当指標は理由付き null |

共通の約束:
- **約定の損益** = profit + commission + swap + fee
- **売買約定** = type が BUY(0) / SELL(1)。入金などの balance(2) は損益に含めない
- **決済約定（取引）** = entry が OUT(1) / INOUT(2) / OUT_BY(3) の売買約定。1 件 = 1 取引
- 時刻はすべてブローカーのサーバ時刻

### 取引指標（MT5 レポートと一致を確認済み: P2_PLAN §1.1）

| 指標 | 定義 | MT5 の対応値 |
|---|---|---|
| `net_profit` | 全売買約定の損益の合計（入場時の手数料も含む） | `STAT_PROFIT` |
| `trades` | 決済約定の件数 | `STAT_TRADES` |
| `win_trades` / `loss_trades` | 決済約定の損益が正 / 負の件数（0 はどちらにも数えない） | `STAT_PROFIT_TRADES` / `STAT_LOSS_TRADES` |
| `gross_profit` / `gross_loss` | 正 / 負の決済損益の合計 | `STAT_GROSS_PROFIT` / `STAT_GROSS_LOSS` |
| `profit_factor` | gross_profit ÷ \|gross_loss\|（負けがなければ null） | `STAT_PROFIT_FACTOR` |
| `expected_payoff` | 決済損益の平均 | `STAT_EXPECTED_PAYOFF` |
| `win_rate`、`avg_win`、`avg_loss` | 勝率、正の平均、負の平均 | （レポートのみ） |
| `max_consec_wins`、`max_consec_wins_amount` | 正の決済損益が続いた最長回数と、その連続の合計額。損益 0 は連続を切る | `STAT_MAX_CONWINS`、`STAT_MAX_CONPROFIT_TRADES`（最長の連勝の金額。`STAT_CONPROFITMAX` は「最も利益の大きい連勝」で別の値） |
| `max_consec_losses`、`max_consec_losses_amount` | 負の決済損益が続いた最長回数と、その合計額 | `STAT_MAX_CONLOSSES`、`STAT_MAX_CONLOSS_TRADES` |
| `fixed_lot`、`distinct_volumes` | 売買約定のロットが 1 種類だけか | — |

注: 入場時に手数料がかかる口座では、MT5 が取引指標（総利益など）を入場手数料込みで数えるかどうかは未確認です（検証に使ったデモ口座は手数料 0）。`net_profit` は全売買約定を合計するので、`STAT_PROFIT` との一致は手数料の有無によらず成り立ちます。

### 残高ドローダウン

| 指標 | 定義 | MT5 |
|---|---|---|
| `balance_max_dd` | 初期資金から売買約定ごとに積み上げた残高曲線の、最高値からの最大下落幅 | `STAT_BALANCE_DD` |
| `balance_max_dd_pct` | 上記の下落幅 ÷ そのときの最高値 × 100 | — |
| `balance_max_dd_days` | 最高値から回復（最高値の更新）まで、またはテスト終了までの最長日数 | — |
| `final_balance` | 最終残高 | — |

### エクイティ・リスク（Telemetry v2 の EA 追跡値）

| 指標 | 定義 | MT5 |
|---|---|---|
| `equity_max_dd` | ティックごとのエクイティの最高値からの最大下落幅 | `STAT_EQUITY_DD` |
| `equity_max_dd_pct`、`equity_peak` | 上記の率、エクイティの最高値 | — |
| `recovery_factor` | net_profit ÷ equity_max_dd（**MT5 と同じ定義**。残高 DD ではない） | `STAT_RECOVERY_FACTOR` |
| `max_floating_loss` | 含み損益の最小値（負の値） | — |
| `max_positions`、`max_lots` | 同時保有ポジション数・ロット合計の最大 | — |
| `min_margin_level` | ポジション保有中の証拠金維持率の最小値（%） | — |
| `stop_out_deals` | `DEAL_REASON_SO` の約定数 | — |
| `bankrupt` | エクイティの最小値 ≤ 0 | — |

### 日次系列（Telemetry v2）

| 指標 | 定義 |
|---|---|
| 日次損益 | 日終値エクイティの前日比（初日は初期資金との差）。日 = ティックのあったサーバ時刻の日 |
| 日次リターン | 日次損益 ÷ 初期資金（固定ロット・複利なしの前提、ARCHITECTURE §12.1） |
| `days` | 日数 |
| `daily_mean_return`、`daily_std_return` | 平均、標本標準偏差（ddof = 1） |
| `daily_sharpe` | 平均 ÷ 標本標準偏差（**年率化しない**。PSR/DSR の入力と同じ頻度 [S-PSR][S-DSR]） |
| `daily_skewness` | `scipy.stats.skew(bias=True)` |
| `daily_kurtosis` | `scipy.stats.kurtosis(fisher=False, bias=True)`（超過でない尖度。PSR の式の γ₄） |

MT5 のシャープレシオ（`STAT_SHARPE_RATIO`）は計算定義が不明なため比較しない（`NOT_COMPARABLE`）。

### 期間別（`period_performance`）

年（`YEAR`, `YYYY`）・月（`MONTH`, `YYYY-MM`）ごとに、売買約定の損益合計、決済件数、その期間内の残高最大ドローダウン（期間開始時の残高から積み上げ）。

### 照合（`metric_check`）

MT5 の値は小数 2 桁で届くので、自前の値も小数 2 桁に丸めて比べ、差が 0.01 以内なら `MATCH`。
状態: `MATCH` / `MISMATCH` / `MT5_MISSING`（MT5 側に値がない） / `OURS_MISSING`（自前で計算できない） / `NOT_COMPARABLE`。
