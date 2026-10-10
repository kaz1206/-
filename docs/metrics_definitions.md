# 指標定義（MetricEngine）

実装: `src/robustlab/metrics/engine.py`（純粋関数）、照合: `src/robustlab/metrics/compare.py`。
定義を変えたら `METRIC_DEF_VERSION` を上げ、本書に新しい版の節を追加する（古い計算結果は消さない）。

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
