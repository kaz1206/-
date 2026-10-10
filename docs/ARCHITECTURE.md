# MT5 EA ロバスト性評価基盤 — アーキテクチャ設計書

- 版: v0.3 — v0.2 で一次情報・原著研究のレビューを反映。v0.3 で P1 計画の不整合（D1〜D8）、再現性の比較方式（E1）、実機確認の結果（F1〜F8、[verification/p1/RECORD.md](../verification/p1/RECORD.md)）を反映（C18〜C34）。変更点は [DESIGN_CHANGELOG.md](DESIGN_CHANGELOG.md)、根拠は [research/FINDINGS.md](research/FINDINGS.md) と [research/SOURCES.md](research/SOURCES.md)。本文中の `[S-xxx]` は情報源 ID、`(C#)` は変更番号
- 対象: MT5 Strategy Tester をバックテストエンジンとして使い、EA のロバスト性・過学習耐性・DD/破産リスクを体系的に評価し、最終的に資金配分まで決定する研究基盤

---

## 0. エグゼクティブサマリー（まずここだけ読めば全体像がわかる）

この基盤の考え方を一言で言うと、
**「一番儲かったパラメータを探す装置」ではなく、「生き残る理由を説明できる候補だけを、証拠付きで次の段階に進める装置」** です。

設計の背骨は次の 6 つです。

| # | 背骨となる原則 | 具体的な仕組み |
|---|---|---|
| 1 | **評価（Evaluation）と判定（Decision）を分ける** | 指標を計算するモジュールは合否を出さない。合否は Gate / Selector だけが出し、理由コード付きで追記保存する |
| 2 | **すべてを追記型（append-only）で保存** | 候補・実行・評価・判定は上書きしない。「なぜ落ちたか」「なぜ残ったか」を後から完全に再現できる |
| 3 | **OOS（ホールドアウト）は物理的に封印** | ジョブ生成層でホールドアウト期間を含むバックテスト要求を拒否する。OOS ステージだけが一度きり解錠できる |
| 4 | **"点" ではなく "領域" を評価** | 候補自身のスコアではなく、周辺パラメータの下位分位点（例: 25%点）を「ロバストスコア」として選抜に使う |
| 5 | **試行回数を台帳で数える** | Deflated Sharpe 等の多重検定補正の入力となる「何回試したか」を、捨てた試行も含めて記録する |
| 6 | **資金配分は "期待値" ではなく "不確実性込みの下限" と "DD 制約" で決める** | Kelly はそのまま使わず、期待値の信頼下限・Fractional 化・Monte Carlo DD 制約の最小値を採用する |

パイプラインの全体像:

```
[Study定義・凍結] → ①候補生成(MT5最適化) → ②結果収集 → ③初期フィルタ
   → ④ロバストネス評価 → ⑤統計評価 → ⑥候補選択
   → ⑦複数期間検証(固定パラメータ) → ⑧OOS(一度きり)
   → ⑨リスク評価 → ⑩資金配分 → ⑪ポートフォリオ評価 → (将来) ⑫実運用モニタリング
```

---

## 目次

1. 全体アーキテクチャ
2. モジュール構成
3. 各モジュールの責務
4. モジュール間インターフェース
5. データモデル
6. データフロー
7. OOS 分離方法
8. 候補 ID・履歴管理
9. ロバストネス評価設計
10. 統計評価設計
11. 選択アルゴリズム設計
12. Risk Allocation 設計
13. Portfolio Risk 設計
14. MT5 実行管理設計
15. エラー処理・Resume 設計
16. MVP と将来拡張の境界
17. 推奨ディレクトリ構成
18. CLI 設計
19. テスト戦略
20. 実装順序
21. 批判的レビュー（潜在的問題・統計的落とし穴・過剰設計・追加すべき要素）

付録 A: 用語集 / 付録 B: 推奨ライブラリ

---

## 1. 全体アーキテクチャ

### 1.1 レイヤー構成

```
┌──────────────────────────────────────────────────────────────────────┐
│ Interface Layer      CLI (Typer) / レポート生成 (HTML) / Notebook       │
├──────────────────────────────────────────────────────────────────────┤
│ Orchestration Layer  Pipeline Runner / Stage Registry / Job Queue     │
│                      （ステージを順に呼ぶだけ。ロジックは持たない）           │
├──────────────────────────────────────────────────────────────────────┤
│ Domain Layer (純粋Python, MT5非依存, テスト容易)                          │
│   Filtering │ Robustness │ Statistics │ Selection │ Validation │      │
│   Risk │ Allocation │ Portfolio │ MetricEngine                        │
├──────────────────────────────────────────────────────────────────────┤
│ Execution Layer      MT5 Adapter (ini/set 生成, Terminal Pool,         │
│                      プロセス監視, ログ解析, レポート/テレメトリ解析)          │
├──────────────────────────────────────────────────────────────────────┤
│ Persistence Layer    SQLite (メタデータ・評価・判定・ジョブ)               │
│                      Artifact Store (Parquet: 取引/日次損益, 生レポート)    │
├──────────────────────────────────────────────────────────────────────┤
│ MQL5 Side            Telemetry Include (.mqh) — EAに組み込む計測ハーネス │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 設計上の要点

- **Domain Layer は MT5 を知らない。** 入力は「取引リスト」「日次損益系列」「サマリ指標」という正規化済みデータのみ。これにより統計・ロバストネス・配分ロジックは Windows/MT5 なしで単体テストできる。将来、別のバックテストエンジン（Python 製や他プラットフォーム）に差し替えることも可能。
- **Orchestrator はステージを呼ぶだけ。** ステージ同士は直接呼び合わず、必ず DB を介して受け渡す（疎結合）。どのステージも単独で CLI から実行できる。
- **MT5 側に小さな計測ハーネス（Telemetry Include）を置く。** MT5 標準レポートだけでは、最適化の各パスの日次損益・最大含み損・最大ポジション数などが取れないため。これが統計評価（DSR/PBO）の精度を決める重要部品（§14.6）。

### 1.3 中心となる 4 つの概念

| 概念 | 意味 | 例 |
|---|---|---|
| **Study** | 1 つの研究単位。戦略・銘柄・期間分割・評価方針・選択方式を含む設定一式。凍結（freeze）後は変更不可 | 「MA_Cross v3 / EURUSD H1 / 2026Q4 研究」 |
| **Candidate** | 戦略バージョン × パラメータ × 銘柄 × 時間足。**期間を含まない** | MA_Cross v3, fast=12, slow=48, EURUSD, H1 |
| **Run** | Candidate を特定の期間・特定の実行環境（コスト・モデル）で実行した 1 回の結果 | 上の候補を 2020-2021 / Real ticks で実行 |
| **Decision** | あるステージで候補に下された判定（PASS/FAIL/WARN）と理由コード | Stage=ROBUSTNESS, FAIL, `NEIGHBORHOOD_UNSTABLE` |

「候補」と「実行」を分けるのが重要です。同じ候補を複数期間・複数コスト条件で実行し、その結果群を束ねて評価するためです。

---

## 2. モジュール構成

| # | モジュール | パッケージ名（案） | 層 | MVP |
|---|---|---|---|---|
| 0 | Core（ドメイン型・ID・理由コード・列挙型） | `core` | Domain | ✅ |
| 0 | Config（YAML 読込・スキーマ検証・ハッシュ） | `config` | Domain | ✅ |
| 0 | Storage（SQLite リポジトリ・Artifact Store・マイグレーション） | `storage` | Persistence | ✅ |
| 0 | MetricEngine（取引/損益から指標を再計算） | `metrics` | Domain | ✅ |
| 1 | Candidate Generation（最適化ジョブ生成・摂動候補生成） | `generation` | Domain+Exec | ✅ |
| — | MT5 Adapter（実行管理） | `mt5` | Execution | ✅ |
| 2 | Result Collection（レポート/テレメトリ取込・正規化） | `collection` | Domain+Exec | ✅ |
| 3 | Initial Filtering（ハードゲート） | `filtering` | Domain | ✅ |
| 4 | Robustness Evaluation | `robustness` | Domain | ✅（一部） |
| 5 | Statistical Evaluation | `statistics` | Domain | ✅（PSR/DSR のみ） |
| 6 | Candidate Selection（交換可能な Selector） | `selection` | Domain | ✅ |
| 7 | Multi-Period Validation | `validation` | Domain+Exec | ✅ |
| 8 | Out-of-Sample Evaluation（Holdout Guard 含む） | `oos` | Domain+Exec | ✅ |
| 9 | Risk Evaluation（MC/Bootstrap） | `risk` | Domain | ✅ |
| 10 | Risk Allocation（交換可能な Allocator） | `allocation` | Domain | ✅（固定比率・DD制約・Fractional Kelly） |
| 11 | Portfolio Management | `portfolio` | Domain | 🔶（相関・同時DDのみ） |
| 12 | Live Monitoring（実運用再評価） | `live` | Domain+Exec | ❌ 将来 |
| — | Orchestration（Pipeline / Job Queue） | `orchestration` | Orchestration | 🔶（手動実行→後で連結） |
| — | Reporting（候補ドシエ・ステージ要約） | `reporting` | Interface | ✅（簡易） |
| — | CLI | `cli` | Interface | ✅ |

---

## 3. 各モジュールの責務

各モジュールについて「やること」「やらないこと（責務の境界）」を明示します。境界を守ることが疎結合の実体です。

### 3.0 共通基盤

**Core**
- やること: ドメイン型（Candidate, Run, Evaluation, Decision 等）、ID 生成規則、理由コード（ReasonCode）カタログ、ステージ列挙。
- やらないこと: I/O、計算。

**Config**
- やること: Study / Strategy / Environment / Selection / Allocation の YAML をスキーマ検証（pydantic）し、正規化 JSON のハッシュ（`config_hash`）を作る。`freeze` 後の変更を検出して拒否。
- やらないこと: 値の解釈（各モジュールが自分の設定セクションを解釈する）。

**Storage**
- やること: SQLite への追記、Parquet アーティファクトの保存（内容ハッシュでファイル名を決める＝Content-Addressed）、スキーママイグレーション。
- やらないこと: ビジネスルール。**UPDATE/DELETE は Job 状態などの運用テーブルに限定**し、研究結果テーブルは INSERT のみ。

**MetricEngine**
- やること: 取引リスト・日次損益から全指標を**自前で再計算**する（MT5 の Sharpe 等は計算定義が不透明なため、参照値として `mt5_*` に別保存し照合のみ）。指標定義にはバージョン（`metric_def_version`）を付与。
- やらないこと: 合否判定。

### 3.1 Candidate Generation
- やること:
  - Study 設定のパラメータ空間から MT5 最適化ジョブ（`.ini` + `.set`）の**仕様**を作る。
  - ロバストネス評価用の**摂動候補**（周辺パラメータ）を生成し、`parent_candidate_id` を付与する。
  - 最適化ラウンド（Optimization Round）を記録する。
- やらないこと: MT5 の起動（MT5 Adapter の責務）、結果の良し悪し判断。
- 方針: MVP は **完全グリッド（粗いステップ）** を推奨。遺伝的最適化は乱数シードを制御できず再現性が低いため「探索用」に限定し、有望領域は後でグリッドで再確認する。

### 3.2 Result Collection
- やること: 最適化 XML レポート、単一テストレポート、Telemetry 出力（CSV/バイナリ）、テスターログを読み込み、正規化して Run / RunMetrics / Artifact として保存。**期待パス数と実パス数の照合**（取りこぼし検知）。
- やらないこと: フィルタリング。取り込めないデータは捨てずに `QUARANTINED` として保存し理由を残す。

### 3.3 Initial Filtering
- やること: 明らかに不適格な候補を安価なルールで除外する（破産・マージンコール、DD 上限超過、統計的に不十分な取引数、期待値マイナス、異常値など）。**全ゲートを評価し、該当する理由コードをすべて記録**（最初の 1 つで止めない）。
- やらないこと: ランキング・最良選択。ここは「足切り」専用。
- 取引数基準は戦略タイプ別プロファイル（§9.6）に従う。

### 3.4 Robustness Evaluation
- やること: パラメータ周辺の安定性、感度、プラトー（平坦領域）の大きさ、コスト耐性、サブ期間一貫性、利益集中度を**評価値として計算**する。必要な追加実行（摂動・コストストレス）はジョブとして要求する。
- やらないこと: 合否判定（スコアを出すだけ。判定は Gate/Selector）。

### 3.5 Statistical Evaluation
- やること: PSR / DSR（MVP）、将来的に PBO / SPA / Bootstrap 信頼区間を計算。**試行回数台帳（Trial Ledger）**から多重検定の分母を取得。
- やらないこと: 試行回数の推測で楽観側に倒すこと（不明時は保守的に大きい値を使う）。

### 3.6 Candidate Selection
- やること: 評価値を入力に、次段階へ進める候補集合を決める。Selector は差し替え可能（Hard Gate / Lexicographic / Pareto / Weighted / Region-Representative）。**選抜予算（上限数）**を持ち、後段の多重検定負荷を制御。
- やらないこと: 評価値の計算、パラメータの変更。

### 3.7 Multi-Period Validation
- やること: 選択された候補を**パラメータ固定のまま**、Discovery 期間と異なる複数の Validation 期間で実行・評価。期間間の一貫性（全期間でプラスか、最悪期間の DD、レジーム別成績）を評価。
- やらないこと: 再最適化（設計上、パラメータを変更する API を持たない）。

### 3.8 Out-of-Sample Evaluation
- やること: Holdout Guard の解錠、OOS マニフェスト（対象候補の固定リスト）作成、一度きりの実行、結果の封印。OOS 結果が「事前予測分布と矛盾しないか」の検定。
- やらないこと: OOS 結果に基づくパラメータ変更・再選択（行えば、その候補は `HOLDOUT_CONTAMINATED` になる）。

### 3.9 Risk Evaluation
- やること: 取引・日次損益の Monte Carlo / Bootstrap により、DD 分布（95%/99% 点）、回復期間、連敗数分布、破産確率、最大含み損、証拠金維持率の下限などを「**1 リスク単位あたり**」で算出し `RiskProfile` として保存。
- やらないこと: ロットの決定。

### 3.10 Risk Allocation
- やること: RiskProfile と口座状態・制約から、EA ごとのリスク比率・ロットを計算。Allocator は差し替え可能（Fixed Fraction / Vol Target / DD-Constrained / Fractional Kelly / 将来: Multi-Kelly, ERC）。**計算根拠をすべて記録**。
- やらないこと: 発注。最小ロット未満を勝手に切り上げること（`INFEASIBLE_MIN_LOT` として明示）。

### 3.11 Portfolio Management
- やること: EA 間相関（通常時・下落時）、同時 DD、同一銘柄エクスポージャの集約、証拠金の同時使用、ポートフォリオ全体の DD 分布（同時ブロックブートストラップ）、全体スケール係数の算出。
- やらないこと: 個別 EA の評価のやり直し。

### 3.12 Live Monitoring（将来）
- やること: MT5 Python API で約定履歴を取得し、magic 番号→candidate_id の対応で実績を蓄積。バックテスト予測分布との乖離検知、リスク量変更の**提案**（人間承認付き）。

---

## 4. モジュール間インターフェース

> 実装コードではなく「契約（入力・出力・副作用）」として記述します。

### 4.1 統一ステージ契約

すべてのステージは同じ形の契約を持ちます。これにより Orchestrator はステージの中身を知らずに順番に呼べます。

| 項目 | 内容 |
|---|---|
| 入力 | `StageContext`（study_id, stage 名, 設定セクション, Repository への読み取りハンドル, 実行 ID）＋ 入力候補 ID 集合 |
| 出力 | `StageResult` = { 追加された Evaluation 群, 追加された Decision 群, 新規に要求された Job 群, 次ステージへの候補 ID 集合, 統計サマリ } |
| 副作用 | Repository への追記のみ。他ステージの呼び出し禁止 |
| 冪等性 | 同じ入力ハッシュ（候補集合＋設定ハッシュ＋コードバージョン）で再実行したら同じ結果。既に完了していればスキップ |
| 前提条件チェック | 必要な前段 Decision が存在するか（例: OOS は SELECTED_FOR_OOS が必要） |

ステージは内部で次の 2 種類の部品を組み合わせます。

| 部品 | 入力 | 出力 | 性質 |
|---|---|---|---|
| **Evaluator** | Candidate + Run 群（＋その Artifact） | `Evaluation`（名前・バージョン・数値群） | 純粋関数。判定しない |
| **Gate** | Evaluation 群 + 閾値（戦略プロファイル考慮） | `GateOutcome`（PASS/FAIL/WARN・理由コード・実測値・閾値） | 純粋関数 |
| **Selector** | 候補ごとの Evaluation 群 + GateOutcome 群 + 選抜予算 | `SelectionResult`（選抜・不選抜・順位・説明） | 純粋関数・決定的（同点処理を規定） |
| **Allocator** | RiskProfile 群 + 口座状態 + 制約 | `AllocationPlan`（EA 別リスク比率・ロット・根拠） | 純粋関数 |

### 4.2 MT5 Adapter との境界

| 方向 | 契約 | 内容 |
|---|---|---|
| Domain → Exec | `BacktestJobSpec` | 戦略バージョン、パラメータ（固定値 or 最適化範囲）、銘柄、時間足、期間、モデル（Real ticks 等）、遅延、初期資金、レバレッジ、通貨、最適化方式・基準、期待パス数、タイムアウト、**用途ステージ**（Holdout Guard 判定用） |
| Exec → Domain | `JobOutcome` | 状態（SUCCEEDED / FAILED / TIMED_OUT / PARTIAL）、エラー分類、生成ファイルパス、ログパス、所要時間、端末ビルド番号 |
| Collection → Storage | `NormalizedRun` | Run メタデータ、正規化指標、取引リスト（Parquet）、日次損益（Parquet）、MT5 原指標（照合用） |

`BacktestJobSpec` は正規化 JSON のハッシュ（`spec_hash`）を持ち、**同一 spec_hash のジョブは再実行せず既存結果を再利用**します（ただし MT5 キャッシュとは別に、こちら側で管理）。

### 4.3 プラグイン（交換可能部品）の登録

- Evaluator / Gate / Selector / Allocator / 統計手法は **名前とバージョンでレジストリに登録** し、YAML から名前で指定。
- 例: `selection.method: pareto_v1`、`allocation.method: dd_constrained_fractional_kelly_v1`
- 判定記録には必ず `component_name` と `component_version` を保存 → 「どのロジックで選ばれたか」を後から再現可能。
- MVP では単純な辞書レジストリで十分（Python entry points によるプラグイン化は将来）。

---

## 5. データモデル

### 5.1 保存方式の使い分け

| データ | 保存先 | 理由 |
|---|---|---|
| メタデータ・指標・評価・判定・ジョブ | **SQLite**（WAL モード） | 単一ファイル・トランザクション・SQL で追跡可能 |
| 取引リスト・日次損益・最適化パス別日次損益行列 | **Parquet**（Artifact Store） | 数百万行でも高速、列指向、pandas と親和性 |
| 生レポート（XML/HTML）、ログ、ini/set | ファイル（Artifact Store、内容ハッシュ名） | 監査用の原本保存 |

### 5.2 エンティティ一覧（主要カラムのみ）

```
study
  study_id (PK), name, config_hash, config_json, status{DRAFT,FROZEN,CLOSED},
  created_at, frozen_at, code_version(git sha), holdout_policy_json

strategy_version
  strategy_version_id (PK = hash(ex5 + mq5 source)), strategy_name, version_label,
  mq5_hash, ex5_hash, telemetry_version, strategy_profile (例: swing_trend),
  param_schema_json

environment                         -- 実行環境（再現性の核）
  -- (C19) 実行前に決まる「依頼したテスター設定」（ID に含める）と、
  --       実行後に分かる「観測した環境」（端末ビルド・サーバ・銘柄仕様。job に記録）に分ける。
  --       銘柄仕様の照合ハッシュに spread（実行ごとに変わる観測値）は含めない (C31)
  env_id (PK = hash(下記)), terminal_build, broker_server, account_currency,
  deposit, leverage, tick_model{REAL_TICKS,EVERY_TICK,OHLC_M1,OPEN_PRICES},
  execution_delay_ms, symbol_spec_snapshot_hash, cost_model_json

symbol_spec_snapshot                -- 銘柄仕様のスナップショット（スワップ・契約サイズ等）
  snapshot_hash (PK), symbol, captured_at, spec_json

data_period
  period_id (PK), study_id, role{DISCOVERY,VALIDATION,HOLDOUT},
  start_date, end_date, embargo_before_days, embargo_after_days, regime_tags_json

optimization_round
  round_id (PK), study_id, strategy_version_id, symbol, timeframe, period_id,
  env_id, param_space_json, algorithm{FULL_GRID,GENETIC}, criterion,
  expected_passes, actual_passes, job_id, created_at

candidate                           -- 期間を含まない
  candidate_id (PK = hash(strategy_version_id, symbol, timeframe, canonical_params)),
  strategy_version_id, symbol, timeframe, params_json,
  origin{OPTIMIZATION,PERTURBATION,MANUAL,COST_VARIANT},
  source_round_id, parent_candidate_id (NULL可), region_id (NULL可),
  holdout_contaminated (bool), created_at

run                                 -- 候補 × 期間 × 環境 × コストシナリオ
  run_id (PK = hash(candidate_id, period_id, env_id, cost_scenario, source)),
  candidate_id, period_id, env_id, cost_scenario{BASE,SPREAD_x1_5,...},
  source{OPT_PASS,SINGLE_TEST,POSTHOC_COST}, job_id, stage_purpose,
  trades_artifact, daily_pnl_artifact, raw_report_artifact, status

run_metrics                         -- コア指標（幅広テーブル: 型安全・高速）
  run_id (PK), metric_def_version,
  net_profit, gross_profit, gross_loss, profit_factor, sharpe_daily,
  sortino, recovery_factor, max_dd_abs, max_dd_pct, max_dd_duration_days,
  trade_count, trades_per_year, win_rate, avg_win, avg_loss, payoff_ratio,
  expectancy, expectancy_stderr, max_consec_losses, max_consec_wins,
  max_floating_loss, min_margin_level, max_positions, max_lots_open,
  margin_call_flag, stop_out_flag, bankruptcy_flag,
  skewness, kurtosis, profit_top5_share, exposure_time_pct

run_metrics_ext                     -- 拡張指標（EAV: 将来指標の追加用）
  run_id, name, value

mt5_reported_metrics                -- MT5 の原値（照合専用）
  run_id, name, value

period_performance                  -- 期間別パフォーマンス（年・四半期・月・レジーム）
  run_id, bucket_type{YEAR,QUARTER,MONTH,REGIME}, bucket_key,
  net_profit, max_dd, trade_count, sharpe, expectancy

parameter_region                    -- 安定領域（プラトー）
  region_id (PK), round_id, method, method_version, member_count,
  centroid_params_json, medoid_candidate_id, robust_score

evaluation                          -- Evaluator の出力（判定なし）
  evaluation_id (PK), candidate_id, stage, evaluator_name, evaluator_version,
  config_hash, input_run_ids_json, input_hash, values_json, created_at

decision                            -- 判定（追記のみ）
  decision_id (PK), candidate_id, stage, outcome{PASS,FAIL,WARN,SELECTED,NOT_SELECTED},
  component_name, component_version, reason_codes_json,
  details_json (実測値・閾値・順位など), pipeline_execution_id, created_at

reason_code                         -- 理由コードカタログ
  code (PK), category, description, severity

trial_ledger                        -- 多重検定の分母（試行回数台帳）
  ledger_id, study_id, strategy_family, round_id, n_trials_raw,
  n_trials_effective, method, created_at

holdout_access_log                  -- OOS 封印の監査ログ
  access_id, study_id, period_id, manifest_hash, requested_stage,
  approved(bool), reason, actor, created_at

oos_manifest
  manifest_hash (PK), study_id, candidate_ids_json, prediction_bands_json,
  locked_at, executed_at

risk_profile
  risk_profile_id, candidate_id, basis_run_ids_json, risk_unit_def,
  mc_method, mc_config_json, n_sims, seed,
  dd_q50, dd_q95, dd_q99, ruin_prob_at_limits_json, consec_loss_q95,
  recovery_days_q95, mean_daily, std_daily, mean_daily_lcb, created_at

allocation_plan
  plan_id, method, method_version, account_equity, constraints_json,
  created_at, approved_by, approved_at

allocation_item
  plan_id, candidate_id, risk_fraction, lot_size, binding_constraint,
  kelly_raw, kelly_adj, f_dd, confidence_weight, rationale_json

portfolio / portfolio_member / portfolio_evaluation
  （構成、相関行列 Artifact、同時DD統計、全体MC結果、スケール係数）

job                                  -- 運用テーブル（状態更新あり）
  job_id, spec_hash, kind{OPTIMIZATION,SINGLE_TEST}, status, attempts,
  terminal_instance, pid, started_at, heartbeat_at, finished_at,
  exit_code, error_class, error_detail, log_paths_json

pipeline_execution                   -- パイプライン実行単位
  pipeline_execution_id, study_id, stage, input_hash, status, started_at, finished_at

deployment (将来)                    -- 実運用との対応
  deployment_id, candidate_id, account_id, magic_number, start_at, plan_id, status

live_trade / live_evaluation (将来)
```

### 5.3 候補の状態はどこに持つか

`candidate` テーブルに「現在の状態」カラムは**持たない**。現在の状態は「その候補の最新 Decision 群」から導出する（ビュー `candidate_status_v`）。理由:
- 状態の上書きで履歴が失われるのを防ぐ。
- 同じ候補が複数の Study やラウンドで異なる判定を受けても矛盾しない（Decision に study / pipeline_execution を紐付ける）。

### 5.4 理由コード（例）

| カテゴリ | コード | 意味 |
|---|---|---|
| 破綻 | `MARGIN_CALL`, `STOP_OUT`, `BANKRUPTCY` | 証拠金不足・破産 |
| リスク | `DD_EXCEEDS_LIMIT`, `FLOATING_LOSS_EXCEEDS_LIMIT`, `MAX_POSITIONS_EXCEEDED` | DD・含み損・ポジション数上限超過 |
| 統計的十分性 | `INSUFFICIENT_TRADES`, `LOW_PSR`, `LOW_DSR` | 取引数不足・統計的に有意でない |
| ロバスト性 | `NEIGHBORHOOD_UNSTABLE`, `ISOLATED_PEAK`, `COST_FRAGILE`, `PROFIT_CONCENTRATED`, `SUBPERIOD_INCONSISTENT` | 周辺不安定・孤立ピーク・コスト脆弱・利益集中・期間不安定 |
| 検証 | `VALIDATION_PERIOD_LOSS`, `REGIME_FAILURE` | 検証期間での損失・特定レジームでの破綻 |
| OOS | `OOS_BELOW_PREDICTION_BAND` | OOS 実績が事前予測下限を下回る |
| 選抜 | `NOT_SELECTED_BUDGET`, `DOMINATED`, `REDUNDANT_IN_REGION` | 予算外・劣後・同領域重複 |
| データ | `DATA_QUALITY_ISSUE`, `REPORT_PARSE_FAILED`, `MODEL_MISMATCH` | データ品質・解析失敗・ティックモデル不一致 |
| 汚染 | `HOLDOUT_CONTAMINATED` | OOS を見た後に派生した候補 |

---

## 6. データフロー

### 6.1 全体フロー

```
 study.yaml ──freeze──▶ [Study: config_hash固定, 期間分割確定, Holdout封印]
                                │
 ① Generation: param_space ─▶ BacktestJobSpec(OPTIMIZATION, DISCOVERY期間)
                                │                     ▲ Holdout Guard（期間交差チェック）
                                ▼
     MT5 Adapter ─▶ terminal64.exe /config:… ─▶ XML報告 + Telemetry(パス別日次損益)
                                │
 ② Collection ─▶ candidate(全パス) + run(OPT_PASS) + run_metrics + trial_ledger
                                │
 ③ Filtering ─▶ decision(PASS/FAIL+理由)          [全候補が記録される]
                                │ PASS
 ④ Robustness ─▶ 近傍統計(既存パスから) ─┬─▶ 不足なら摂動ジョブ要求 → ①へ（PERTURBATION）
                                        └─▶ コストストレス(事後計算 or 再実行)
                │   evaluation(robust_score, plateau, cost_sensitivity, …)
                ▼
 ⑤ Statistics ─▶ PSR / DSR (trial_ledger 参照)、[将来] PBO, SPA
                ▼
 ⑥ Selection(Selector差替可) ─▶ decision(SELECTED / NOT_SELECTED + 理由), 選抜予算内
                ▼
 ⑦ Multi-Period ─▶ SINGLE_TEST ジョブ(VALIDATION期間群, 固定パラメータ, Real ticks)
                │   evaluation(期間一貫性, レジーム別) → Gate → decision
                ▼
   [OOS予測帯の作成: ⑦までのデータからMCでOOS期間の成績予測分布を作る]
                ▼
 ⑧ OOS ─▶ oos_manifest ロック → Holdout解錠(1回) → SINGLE_TEST(HOLDOUT)
                │   → 予測帯との整合性検定 → decision → 封印
                ▼
 ⑨ Risk ─▶ risk_profile (Validation+OOS の取引/日次損益からMC/Bootstrap)
                ▼
 ⑩ Allocation(Allocator差替可) ─▶ allocation_plan / allocation_item
                ▼
 ⑪ Portfolio ─▶ 相関・同時DD・全体MC → スケール係数 → allocation_plan 改訂
                ▼
 ⑫ (将来) Live ─▶ 実績取込 → 予測帯との乖離検知 → リスク量変更提案
```

### 6.2 どのデータをどのステージで使ってよいか（データ使用ルール）

| ステージ | Discovery | Validation | Holdout |
|---|---|---|---|
| ① 生成 / ② 収集 | ✅ | ❌ | ❌ |
| ③ フィルタ / ④ ロバストネス / ⑤ 統計 / ⑥ 選択 | ✅ | ❌（※1） | ❌ |
| ⑦ 複数期間検証 | 参照のみ | ✅ | ❌ |
| ⑧ OOS | 参照のみ | 参照のみ（予測帯作成） | ✅（1回） |
| ⑨ リスク評価 | ⚠️（楽観バイアスのため既定は除外） | ✅ | ✅ |
| ⑩⑪ 配分・ポートフォリオ | — | ✅（RiskProfile 経由） | ✅（RiskProfile 経由） |

※1 Validation 結果を ⑥ の選抜にフィードバックすると Validation も In-Sample 化する。⑦ の Gate で落とすこと自体は許容するが、「⑦の成績順で並べて上位を採る」ことは禁止（⑦は合否のみ）。この区別は §21 で詳しく扱う。

---

## 7. OOS 分離方法

OOS の価値は「一度でも選抜に影響したら失われる」ため、**運用ルールではなくシステムの構造で守る**のが原則です。ただし人間が手動で MT5 を操作するのは止められないため、技術的ガード＋監査ログ＋事前登録の三重構えにします。

### 7.1 期間分割の設計

```
時間 →
|--V0--|emb|------ DISCOVERY ------|emb|--V1--|emb|--V2--|emb|==== HOLDOUT ====|
 過去検証      最適化に使う唯一の期間          複数期間検証           最新期間・封印
```

- **Holdout は原則として最新期間**に置く（将来に最も近い市場環境で試すため）。
- Validation は Discovery の**前後両方**に置いてよい（パラメータ固定の頑健性確認であり、未来予測ではないため。V0 のような過去側検証はレジーム多様性の確保に有効）。
- **Embargo（緩衝期間）**: 各期間の境界に、戦略の最大保有期間 × 係数（例: 2 倍、最低 5 営業日）の空白を置く。境界をまたぐポジションや長期インジケータによる情報漏れを抑える。
- Validation 期間は**レジームが異なる**ように選ぶ（高ボラ/低ボラ、トレンド/レンジ、金利局面）。レジームタグは `data_period.regime_tags` に記録（MVP は手動タグ、将来は自動判定）。
- 推奨比率（目安）: Discovery 40–50% / Validation 計 25–35% / Holdout 15–25%。ただし**Holdout は統計的検出力を満たす取引数が見込める長さ**にする（取引頻度の低い戦略ほど長く必要）。

### 7.2 Holdout Guard（技術的ガード）

1. **Study 凍結時に Holdout 期間を確定**し、`config_hash` に含める。凍結後は期間変更不可（変更＝新 Study）。
2. **全ジョブは JobBuilder を通る**。JobBuilder は `[from, to]` と Holdout 期間（＋embargo）の交差を検査し、`stage_purpose != OOS` なら**正常な判定結果として拒否**し（例外ではない。§15.1 の `GUARD` と同じ扱い。CLI の終了コードは 3）、`holdout_access_log` に `approved=false` で記録 (C24)。交差判定は、MT5 の ToDate が「含まない」ことに関係なく閉区間で保守的に行う (C28)。
3. **データアクセス層も同様にフィルタ**: レジーム判定や相関計算のために価格データを Python 側で読む場合も、Holdout 区間はマスクされた状態でしか取得できない。
4. **OOS 実行の手順**:
   1. ⑦を通過した候補から OOS マニフェスト（候補 ID リスト＋事前予測帯）を作成しロック（`manifest_hash`）。
   2. CLI で明示的確認（`--i-understand-this-is-final` のようなフラグ）を要求。
   3. マニフェストの候補だけを一度実行。技術的失敗時の再実行は**同一 spec_hash に限り**許可。
   4. 結果を保存し Study 内の Holdout を `BURNED` に変更。
5. **汚染の伝播**: OOS 実行済み候補から派生した候補（パラメータ微修正など）は自動で `holdout_contaminated = true`。これらは同じ Holdout で評価できず、新しい Holdout（新 Study）が必要。
6. **Holdout 使用回数の記録**: 同じ価格区間を Holdout として使った Study の数を数え、レポートに表示する（「この区間は既に 3 回見られている」＝ OOS としての価値が劣化している）。

### 7.3 事前登録（Pre-registration）

- OOS 実行前に「合格基準」を `oos_manifest.prediction_bands_json` に書き込み、ロックする。
- 合格基準は「OOS 利益が最大」ではなく、**「OOS 実績が、⑦までのデータから作った予測分布の下側 5% 点を下回らない」**（＝反証されない）を既定とする。OOS 期間は短く分散が大きいため、絶対値の比較より整合性の検定が適切。

---

## 8. 候補 ID・履歴管理

### 8.1 ID 設計

| ID | 生成規則 | 性質 |
|---|---|---|
| `strategy_version_id` | `sv_` + sha256(ex5 バイナリ ＋ mq5 ソース ＋ telemetry_version) 先頭 16 桁。**mq5 は任意**（ソース非公開の EA は mq5 = null、`source_available=false` を記録）(C20) | EA コードが 1 文字でも変われば別戦略版。旧結果を新コードに流用しない |
| `candidate_id` | `cd_` + sha256(正規化 JSON{strategy_version_id, symbol, timeframe, params}) 先頭 16 桁 | **決定的**: 同じ組み合わせなら必ず同じ ID。重複実行を自動で検出 |
| `run_id` | `rn_` + sha256(candidate_id, period_id, env_id, cost_scenario, source) | 同上 |
| `job_id` / `spec_hash` | spec_hash = sha256(正規化 BacktestJobSpec) | 冪等実行のキー |
| その他（evaluation, decision 等） | ULID（時系列ソート可能な一意 ID） | 追記型レコード用 |

**パラメータ正規化ルール**（ID の決定性に必須）:
- キーをアルファベット順にソート、数値は型を固定（int は int、double は有効桁を決めて丸めた文字列表現）、bool/enum は正規表現に統一。
- 「最適化対象外だが既定値で動くパラメータ」も含めて**全パラメータ**を記録（後で既定値が変わっても区別できる）。
- ただし `RL_` で始まる入力（job_id など Telemetry 用のハーネス入力）は candidate_id の計算から除外する（含めると実行ごとに ID が変わる）(C21)。
- P1（Study・期間テーブルがない段階）の `run_id` は `rn_` + sha256(candidate_id, from_date, to_date, 依頼したテスター設定のハッシュ, cost_scenario, source)。期間は**半開区間 `[from_date, to_date)`（ブローカーサーバ時刻）**として記録する (C19, C28)。

人間向けには `alias`（例: `MAcross_v3_EURUSD_H1_#0421`）も付けるが、照合は必ずハッシュ ID で行う。

### 8.2 系譜（Lineage）

- `candidate.parent_candidate_id`: 摂動候補・コストバリアント・手動派生の親。
- `candidate.source_round_id`: どの最適化ラウンドで生まれたか。
- `candidate.region_id`: どの安定領域に属するか。
- `decision`: ステージごとの判定履歴（時系列）。
- `evaluation.input_run_ids`: どの実行結果から評価が計算されたか。

### 8.3 追跡クエリ（`trace` コマンドの出力イメージ）

```
Candidate cd_3f9a… (MAcross_v3 EURUSD H1 fast=12 slow=48 sl=40)
├─ 生成: round rd_01 (DISCOVERY 2014-2019, FULL_GRID 1,440 passes, env ev_7c…)
├─ ③ Filtering     PASS  (trades=612 ≥ req 180[swing_trend], DD 14.2% ≤ 25%)
├─ ④ Robustness    PASS  robust_score(Q25 of 26 neighbors)=0.81, plateau=19 pts,
│                        cost x2 spread: PF 1.41→1.22 (OK)
├─ ⑤ Statistics    PASS  PSR=0.97, DSR=0.91 (N_trials=1,440 raw / ~85 eff)
├─ ⑥ Selection     SELECTED  pareto_v1 rank 3 / budget 10, region rg_02 medoid
├─ ⑦ Multi-Period  PASS  V0 +, V1 +, V2 − (小損失, DD 9.8%) → WARN SUBPERIOD
├─ ⑧ OOS           PASS  OOS expectancy 0.31R (予測帯 5%点 -0.05R を上回る)
├─ ⑨ Risk          DD q95=18.4R, q99=24.1R, ruin(30R)=0.4%
└─ ⑩ Allocation    risk_fraction=0.42% (binding: DD制約), lot=0.06
```

これにより「どの条件で生成され」「どの検証を通過し」「なぜ最終候補になったか」を 1 画面で説明できます。

---

## 9. ロバストネス評価設計

### 9.1 パラメータ近傍（Neighborhood）の定義

- 各パラメータを**グリッドのインデックス座標**に変換（値そのものではなく「何ステップ離れているか」）。スケールの違うパラメータを公平に扱うため。
- 近傍 `N_k(c)` = チェビシェフ距離 k ステップ以内の点（k=1 で 3^d − 1 点）。
- **次元の呪い対策**: パラメータが 4〜5 個を超えると近傍が爆発するため、
  - (a) 感度の低いパラメータを近傍計算から除外（後述の感度分析で判定）、
  - (b) 各軸 ±1,±2 の **One-At-a-Time（OAT）摂動** ＋ ±k 箱内の **ランダム（Latin Hypercube）摂動** を数十点、
  の組み合わせで近似する。
- 完全グリッドなら近傍は既存パスから取得（追加実行不要）。遺伝的最適化や疎なグリッドでは近傍が欠けるため、**摂動候補を生成して追加実行**（`origin=PERTURBATION`, `parent_candidate_id` 付き）。

### 9.2 ロバストネス指標

| 指標 | 定義 | 解釈 |
|---|---|---|
| **Robust Score** | 近傍（自身含む）のスコア分布の **25% 分位点** | 選抜の主指標。ピークより「周りも悪くない」ことを重視 |
| Neighbor Pass Rate | 近傍のうち初期フィルタを通過する割合 | 低い＝崖っぷちのパラメータ |
| Peak Ratio | 自身のスコア ÷ 近傍中央値 | 大きすぎる（例: > 1.5）＝孤立ピーク（`ISOLATED_PEAK`） |
| Plateau Size | 自身を含む「合格点の連結成分」の点数 | 大きいほど安定領域 |
| Parameter Sensitivity | 各パラメータ 1 ステップ変化あたりのスコア変化（中央値） | どのパラメータが効いているか / 効きすぎていないか |
| Cost Sensitivity | スプレッド ×1.5・×2、追加スリッページ 0.5/1.0 pips でのスコア低下率 | 低下が大きい＝コスト脆弱（`COST_FRAGILE`） |
| Sub-period Consistency | Discovery 内の年別: 黒字年の割合、最悪年の損益、年別 Sharpe の分散 | 特定年だけで稼いでいないか |
| Profit Concentration | 上位 5 取引（または上位 5%）の利益シェア、上位 N 取引除外後の損益 | 少数の大勝ち依存（`PROFIT_CONCENTRATED`） |
| Start-date Sensitivity（任意） | 開始日を数週ずらした複数実行のばらつき | 経路依存性の強い戦略（グリッド系）で重要 |

「スコア」は Study 設定で定義（既定: 日次 Sharpe を基本とし、DD 制約違反は −∞ 扱い）。**PF や純利益単独をスコアにしない**。

### 9.3 安定領域（Parameter Region）

- 合格点（フィルタ PASS かつスコア ≥ 閾値）に対し、グリッド上の連結成分（MVP）または DBSCAN 等（将来）でクラスタリング。
- 各領域の**メドイド**（領域内で他点との距離合計が最小の実在点）を代表候補とする。ピーク点ではなく「領域の中心」を選ぶことで、パラメータが少しずれても成績が崩れにくい候補を選ぶ。
- 領域情報は選抜（§11）で「同一領域からは最大 k 個」の多様性制約に使う。

### 9.4 コストストレスの実装方式

| 方式 | 内容 | 精度 | コスト | MVP |
|---|---|---|---|---|
| **事後計算（Post-hoc）** | 取引リストの各取引から「追加スプレッド × ロット × ティック価値」を差し引く | 近似（エントリー判断が変わる効果は無視） | 極小 | ✅ |
| **再シミュレーション** | カスタムシンボル（スプレッド・手数料を設定）や遅延設定で MT5 再実行 | 高 | 大 | 将来（最終候補のみ） |

指値・逆指値の約定有無が変わる戦略（スキャルピング等）は事後計算の誤差が大きいので、戦略プロファイルで再シミュレーション必須に指定できるようにする。

### 9.5 Walk-Forward の位置付け

ユーザー要件「検証期間ごとに再最適化しない」と Walk-Forward（WFA: 窓ごとに再最適化）は**目的が異なる**ため、混同しないよう分けて扱う。

| 検証 | 評価対象 | 本設計での位置 |
|---|---|---|
| 固定パラメータ複数期間検証 | **パラメータセット**の頑健性 | ⑦（MVP） |
| Walk-Forward Analysis | **最適化手続き**の頑健性（「定期的に再最適化する運用」が機能するか） | 将来・別トラック。再最適化型の運用を検討する場合のみ |

### 9.6 戦略タイプ別プロファイル（一律基準を避ける）

`strategy_profiles.yaml` で戦略タイプごとに基準を定義し、Strategy Version に紐付ける。

| 項目 | scalping | intraday | swing_trend | mean_reversion | grid_martingale |
|---|---|---|---|---|---|
| 期待取引頻度（年） | 500–5,000 | 100–600 | 20–150 | 50–400 | 100–2,000（バスケット単位で数える） |
| 取引数基準 | 統計的十分性で決定（下記） | 同左 | 同左 | 同左 | **バスケット（一連のポジション群）単位** |
| 必須コストストレス | 再シミュレーション | 事後計算 | 事後計算 | 事後計算 | 再シミュレーション |
| 重点リスク指標 | コスト感度・約定遅延 | 時間帯依存 | 利益集中・長期 DD | テール損失 | **最大含み損・最大ポジション数・証拠金維持率・開始日感度** |
| Embargo 係数 | 小 | 小 | 大（保有期間長） | 中 | 中 |
| ブートストラップ単位 | 取引 | 取引/日次 | 日次ブロック | 日次ブロック | **日次ブロック（取引の独立性が成り立たない）** |

**取引数の統計的十分性ルール（固定値の代わり）**:
- 既存の **MinTRL（Minimum Track Record Length）** [S-PSR] を使う (C1)。MinTRL = 1 + [1 − γ̂₃·ŜR + (γ̂₄−1)/4·ŜR²]·(z_α / (ŜR − SR*))²（ŜR は観測頻度のまま、年率化しない）。負の歪度や厚い裾ほど、必要な標本長が自動的に長くなる。
- ゲートの本体は **PSR(SR*=0) ≥ 閾値**（同じ論文の式）。MinTRL はレポートで「あと何日/何取引必要か」を示すのに使う。絶対下限（例: 30）は安全装置としてのみ持つ。
- v0.1 の目安式 `n ≈ (z_α / s)²` は MinTRL を粗く再発明したものだったので撤回した。
- これにより「低頻度だが優位性が大きい戦略」と「高頻度で優位性が薄い戦略」を同じ物差しで公平に扱える。

---

## 10. 統計評価設計

### 10.1 手法の整理表

| 手法 | 目的 | 本基盤での必要性 | 必要データ | 実装難易度 | 区分 | 利用ステージ |
|---|---|---|---|---|---|---|
| **Parameter Stability / Sensitivity** | 最適点が孤立したピークでないか、周辺でも機能するか | **最重要**。MT5 最適化の過学習の主因に直接効く | パス別サマリ（＋摂動実行） | 低〜中 | **MVP** | ④ |
| **Bootstrap / Monte Carlo** | 取引順序・サンプルの偶然性による DD・破産確率・信頼区間の推定 | **最重要**（リスク・配分の根拠） | 取引リスト / 日次損益 | 低〜中 | **MVP**（取引リサンプリング＋日次ブロック） | ⑤ ⑧ ⑨ ⑪ |
| **PSR（Probabilistic Sharpe）** | 観測 Sharpe が基準値を上回る確率（歪度・尖度・標本長を考慮） | 高（取引数ゲートの本体） | 日次損益 | 低 | **MVP** | ③ ⑤ |
| **Deflated Sharpe Ratio** | 多数の試行から最良を選んだことによる Sharpe の選択バイアス補正 | 高（最適化＝大量試行のため必須級） | 全試行の Sharpe 分散・試行回数・選択候補の歪度尖度 | 低〜中（式は単純、**試行回数の正確な把握が難所**） | **MVP**（試行回数は保守的に生の N） | ⑤ |
| **Walk-Forward Analysis** | 再最適化手続きの頑健性 | 中（固定パラメータ運用なら不要） | 窓ごとの最適化実行（計算量大） | 中（MT5 実行回数が多い） | 将来 | 別トラック |
| **Probability of Backtest Overfitting（CSCV）** | 「IS で最良の構成が OOS で中央値未満になる確率」 | 高（選抜手続き全体の過学習度を数値化） | **全試行の T×N 日次損益行列**（Telemetry 必須） | 中 | **Phase 2**（Telemetry Frames 整備後） | ⑤（ラウンド単位の診断） |
| **White's Reality Check** | 最良戦略がベンチマークを上回るのが偶然でないかの検定 | 中（SPA の方が優れる） | 全試行の損益系列 | — | **独立実装しない**。arch の `SPA` から特殊ケースとして得る (C3) [S-ARCH] | ⑤ |
| **Hansen's SPA / Romano-Wolf StepM** | SPA: ラウンドに本物のエッジを持つ候補があるか。StepM: **どの候補が**「取引しない」を有意に上回るか（FWER を制御） | 中〜高 | 全試行の日次損益（Telemetry Frames） | 低（**arch の `SPA`/`StepM` を使う**。入力は損失なので符号を反転。バージョン固定） | **Phase 2〜3**。StepM を優先 (C4) | ⑤ |
| **Combinatorial Purged CV** | 時系列 CV で複数の OOS 経路を作り、性能分布を推定 | 低（ML 型戦略向け。各分割で MT5 再最適化が必要） | 期間グループ別の再最適化結果 | 高 | **不採用**（ML 型 EA を導入するまで保留。導入時は skfolio を使う）(C9) | — |
| （追加提案）Haircut Sharpe（Harvey-Liu） | 多重検定を考慮した Sharpe の割引 | 低（DSR と重複） | DSR と同等 | 低 | **不採用**（検算用に R quantstrat を任意で使う）(C8) | — |
| （追加提案）Entry Randomization / Permutation Test | 同じエグジット・頻度でエントリーをランダム化した「偽戦略」との比較 | 高（エッジがエントリー由来か確認） | EA に乱数エントリーモード | 中 | Phase 2 | ⑤ |

### 10.2 MVP の統計評価の具体像

1. **PSR / MinTRL** [S-PSR]: 各 Run の日次損益から、年率化しない Sharpe・歪度・尖度・標本長を算出し、`PSR(SR* = 0)` と MinTRL を計算する。取引数ゲートに使う。自前実装（式のみ）で、原著の数値例と R PerformanceAnalytics を検算の基準にする。
2. **DSR** [S-DSR]: SR₀ = √V[ŜR]·((1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(Ne)))、DSR = PSR(SR₀)。
   - 試行回数 N: `trial_ledger` の値。**同じ戦略ファミリーで過去に捨てた Study・ラウンドの試行も合算**（研究者の自由度を含める）。MVP は相関を無視した生の N（保守的＝過大補正側）。
   - 試行間 Sharpe の分散: MVP は MT5 のパス別指標から近似する。ただし MT5 の Sharpe は定義が不明なので、**Telemetry Frames を導入するまでは DSR を参考値（WARN）として扱い、FAIL 判定には使わない** (C7)。Phase 2 で Telemetry の日次損益から正確に算出する。
   - 実効試行数（N_eff）: Phase 2 で **López de Prado & Lewis (2019) の相関クラスタリング手法** [S-ONC] を使って推定する (C6)。
3. **Bootstrap 信頼区間**: 期待値・Sharpe・最大 DD の 90% 区間。**Stationary Bootstrap（Politis-Romano 1994）＋ブロック長の自動選択（Politis-White 2004、Patton ら 2009 の補正）**を、arch の `StationaryBootstrap` と `optimal_block_length` で行う (C2) [S-SB][S-PW][S-ARCH]。乱数シードは必ず記録する。
4. **PBO（Phase 2）**: R `pbo` 1.3.5 [S-PBO-R] の CSCV に忠実に移植し、R `pbo` との数値一致を回帰テストにする (C5)。**ラウンド単位の診断**（PBO が高いラウンドから出た候補全体に WARN を付ける）として使い、個別候補の順位付けには使わない。

各手法の評価（何を測るか・仮定・適合性・重複・コスト）は [research/FINDINGS.md §1](research/FINDINGS.md) を参照。

### 10.3 統計手法の差し替え構造

- 統計手法は `StatisticalTest` として登録（名前・バージョン・必要データ種別・出力スキーマ）。
- 「必要データ種別」を宣言させることで、Telemetry がない Run に対して PBO を要求した場合などに**実行前に明確なエラー**を返せる。
- 結果は `evaluation` に保存（`evaluator_name = "dsr"`, `values_json = {dsr, sr_star, n_trials, n_eff, var_sr, …}`）。

### 10.4 多重検定の「見えない試行」を数える

DSR 等が正しく機能するかは、**N（試行回数）をどれだけ正直に数えられるか**で決まります。以下をすべて台帳に入れます。
- MT5 最適化の全パス（失敗パス含む）
- 摂動・コストバリアント実行
- 破棄した Study・やり直したラウンド
- EA コード修正前の版での試行（`strategy_family` 単位で集計）
- 指標や閾値の変更回数（「設定変更ログ」から自動集計し参考値として表示）

---

## 11. 選択アルゴリズム設計

### 11.1 Selector の契約

- 入力: 候補ごとの Evaluation 群、GateOutcome 群、領域情報、選抜予算 `K`、設定。
- 出力: 選抜候補（順位付き）、不選抜候補（理由コード付き）、説明（どの指標が決め手か）。
- 必須性質:
  - **決定的**（同点時は candidate_id の辞書順など規定のタイブレーク）。
  - **選抜予算を持つ**（後段の検証・OOS の多重検定負荷と計算コストを制御）。
  - **ハードゲート失格者は入力に含めない**（ゲートと選抜の責務分離）。

### 11.2 実装する Selector

| Selector | 概要 | 長所 | 短所 | 区分 |
|---|---|---|---|---|
| `hard_gate_v1` | 全ゲート PASS の候補を Robust Score 降順で上位 K | 単純・説明容易 | 単一指標依存 | **MVP** |
| `lexicographic_v1` | 優先順位付きの指標で順に絞る（例: DD 制約 → DSR → Robust Score） | 優先順位が明確 | 閾値の恣意性 | **MVP** |
| `pareto_v1` | Robust Score↑, DD q95↓, DSR↑, Cost Sensitivity↓ の非劣解集合 → 混雑距離で K 個 | 重みの恣意性なし、トレードオフ可視化 | 説明がやや難 | **MVP 推奨既定** |
| `region_representative_v1` | 安定領域ごとにメドイドを選び、領域の Robust Score で順位付け | 多様性・プラトー重視 | 領域推定に依存 | MVP（pareto と組み合わせ） |
| `weighted_rank_v1` | 各指標を**順位正規化**して加重和 | 柔軟 | 重み自体が過学習対象になる | 将来 |
| `correlation_diverse_v1` | 選抜済み候補との損益相関が高いものを減点（貪欲法） | ポートフォリオ適性 | 日次損益が必要 | Phase 2 |

### 11.3 選抜の多段構成（推奨既定）

```
ゲート通過候補
  → 領域ごとにメドイド抽出（region_representative）
  → Pareto 非劣解（Robust Score, DD q95, DSR, Cost Sens.）
  → 同一領域から最大 k 個、全体 K 個（混雑距離で多様性確保）
  → SELECTED（予算外は NOT_SELECTED_BUDGET、劣後は DOMINATED）
```

### 11.4 Selector 自体の過学習を防ぐ

- Selector 名・バージョン・設定は Study 凍結時に固定。Study 途中で Selector を変えて結果を見比べる行為は「試行」として台帳に記録。
- Selector の性能は**合成データ（既知の真のエッジを持つ戦略＋ノイズ戦略）**で事前評価する（§19.4）。

---

## 12. Risk Allocation 設計

### 12.1 前提: 「リスク単位」で正規化する

- 評価用バックテストは**固定ロット（複利なし）**で実行し、結果を「1 リスク単位あたり」の損益に変換する。複利運用のバックテストは経路依存で DD がロットサイズと絡み合い、配分計算に使えないため。
- リスク単位（`risk_unit_def`）の候補:
  - (a) 1 ロットあたり損益（シンプル）
  - (b) 1 取引のストップ幅を 1R とした R 倍数（SL を持つ戦略向け）
  - (c) 日次損益の標準偏差を 1 とした単位（Vol Target 用）
- EA 側に「評価モード＝固定ロット」を必須機能として持たせる（Telemetry Include のチェック項目）。

### 12.2 Allocator の種類

| Allocator | 計算の考え方 | 不確実性の扱い | 区分 |
|---|---|---|---|
| `fixed_fraction_v1` | 1 取引の想定最大損失（SL or q99 損失）を口座の f% に | なし（f は人が決める） | **MVP** |
| `dd_constrained_v1` | MC の DD 分布から「P(DD > 許容 DD L) ≤ α」となる最大の f を求める。固定ロットでは DD がほぼ f に比例するので f_DD = L / DD_q(1−α)（1 単位あたり） | DD 分布の裾で吸収 | **MVP（最重要）** |
| `fractional_kelly_v1` | f_K = μ_LCB / σ²（連続近似）× 分数（既定 0.25〜0.5） | μ に**信頼下限（Bootstrap 下側 25% 点など）**を使う、DSR/OOS 整合性で信頼度係数を掛ける | **MVP** |
| `vol_target_v1` | 目標ボラ ÷ 推定ボラ でスケール | EWMA ボラ等 | Phase 2 |
| `multi_rck_v1` | **多資産 Risk-Constrained Kelly**（Busseti-Ryu-Boyd 2016、cvxpy で凸最適化）。v0.1 の `multi_kelly`（Σ⁻¹μ）は平均の推定誤差に極端に弱いので置き換えた (C11) [S-RCK][S-MTZ] | DD 制約を直接扱う・μ の下限 | 将来 |
| `risk_parity_v1`（ERC） | リスク寄与均等 | μ を使わない＝推定誤差に強い | 将来 |

### 12.3 最終リスク比率の決定ルール（MVP 既定）

```
f_kelly_adj = fraction × max(0, μ_LCB) / σ²
f_dd        = L_ea / DD_q99_per_unit            （EA 単体許容 DD から）
f_cap       = 設定上限（例: 1 EA あたり口座の 1%/取引 相当）
f_raw       = min(f_kelly_adj, f_dd, f_cap)
f_final     = f_raw × confidence_weight × ramp_up_factor
```

**このルールの位置付け (C10)**: これは独自の発明ではなく、**Risk-Constrained Kelly [S-RCK] の 1 変数版**です。単一 EA では成長率が f について凹で、DD 制約は f が大きいほど厳しくなる（単調）ので、最適解は「無制約の Kelly 解」と「制約を満たす最大の f」の小さい方になります。原著との違いは次の 2 点で、どちらも意図的です。
- 制約: 原著は「初期資産からの下落確率」、本基盤は「ピークからの DD」（実運用の停止判断に直結するため）。
- 解き方: 原著は凸近似、本基盤は MC で直接評価（1 変数なので可能）。

μ_LCB と分数化の根拠は、Kelly では平均の推定誤差の影響が支配的であること（平均:分散:共分散 ≈ 20:2:1）[S-MTZ]。役割を分けて記録します: **μ_LCB = 推定誤差への備え、分数 = モデル誤差（非定常性）への備え**。μ_LCB を使う場合の分数の既定値は 0.5。

- `confidence_weight`（0〜1）: DSR、OOS の予測帯整合性、取引数（標本の大きさ）から決定。証拠が薄いほど小さい。
- `ramp_up_factor`: 実運用開始直後は 0.25 などから開始し、実績の蓄積と整合性確認に応じて段階的に引き上げ（§12.5）。
- **どの制約が効いたか（binding_constraint）を必ず記録**。多くの場合 DD 制約が効くはずで、それが健全な状態。
- μ_LCB ≤ 0 の場合は Kelly 系は 0 を返し、「配分しない」が正解であることを明示（他の Allocator で無理に配分しない）。

### 12.4 ロットへの変換

```
lot = floor_to_lot_step( f_final × Equity / RiskPerLot )
```
- `RiskPerLot`: リスク単位定義に応じて、1 ロットあたり DD_q99 または 1 取引 SL 損失額。**金額換算と必要証拠金は MetaTrader5 API の `order_calc_profit` / `order_calc_margin` に任せ、ロットの最小値・刻みは `symbol_info` から取る**。ティック価値からの自前換算はしない (C12) [S-MT5PY]。
- チェック: 最大同時ポジション時の必要証拠金 ≤ 証拠金予算、`lot ≥ min_lot`。満たさない場合は**切り上げず** `INFEASIBLE_MIN_LOT` / `MARGIN_BUDGET_EXCEEDED` として報告（小口座で過大リスクになるのを防ぐ）。
- 口座通貨と銘柄の決済通貨が異なる場合の換算レートは、計算時点の値を記録（再現性）。

### 12.5 実運用後の再評価（構造のみ、実装は将来）

- `deployment` テーブルで magic 番号 ↔ candidate_id を対応付け。
- 実績の取込 → 以下を定期評価:
  - **DD コーン**: 実績 DD が MC 予測の 95%/99% 帯を超えたらリスク縮小 / 停止を提案。
  - **期待値ドリフト**: CUSUM / 逐次確率比検定（SPRT）で期待値の低下を検知。
  - **実コスト**: 実スリッページ・スプレッドがバックテスト想定を超えていないか。
  - **取引頻度の異常**: 予測より極端に多い/少ない＝環境変化 or バグ。
- 期待値の推定は**バックテストを事前分布、実績を尤度とするベイズ更新**（事前分布は DSR 等で割り引いたもの）にすると、実績の少ない初期に過剰反応しない。
- リスク量の変更は**提案 → 人間承認 → 新 allocation_plan** の流れで、自動で増額はしない（減額・停止のみ自動化を許可するのが安全）。

---

## 13. Portfolio Risk 設計

### 13.1 評価項目

| 項目 | 方法 | MVP |
|---|---|---|
| 相関（通常時） | 日次損益の Pearson / Spearman | ✅ |
| 相関（下落時） | 片方または全体が DD 中の日に限った相関、下側テール依存 | ✅（簡易） |
| 同時 DD | 「両 EA が同時に DD x% 以上にある日数の割合」、DD 期間の重なり | ✅ |
| 銘柄エクスポージャ集約 | 同一銘柄・同一通貨（USD 等）の方向別ロット合計 | ✅ |
| 証拠金の同時使用 | 日次の最大同時必要証拠金（Telemetry の最大ポジションから推定） | 🔶 |
| ポートフォリオ全体 DD 分布 | **同日付ブロックブートストラップ**（全 EA の同じ日付ブロックをまとめてリサンプル＝相関構造を保持） | ✅ |
| 寄与分析 | 全体 DD への EA 別寄与（Component DD / CVaR 寄与） | 将来 |
| ストレスシナリオ | 過去の危機期間（例: 2015 SNB、2020 3月、2022 金利急騰）の同時成績 | 🔶（期間タグで抽出） |

### 13.2 全体スケール調整

1. 個別 Allocator で `f_final` を決定。
2. 全体 MC で `P(ポートフォリオ DD > L_portfolio) ≤ α_p` を満たすか確認。
3. 満たさなければ全体スケール係数 `k ≤ 1` を二分探索で求め、全 EA に掛ける（MVP）。
4. 将来: 相関の高い EA 群をクラスタ化し、クラスタ単位にリスク予算を割り当てる（Hierarchical Risk Parity 的手法）。

### 13.3 注意点

- 相関は危機時に上昇する。**通常時相関で分散効果を見積もらない**。下落時相関・ストレス期間の同時成績を優先して評価する。
- 日次損益が存在しない日（取引なし）を 0 で埋めるか欠損にするかで相関が変わる。**口座評価額ベース（含み損益込み）の日次変化**を基本とする（Telemetry で日次エクイティを出す理由の一つ）。

---

## 14. MT5 実行管理設計

### 14.1 方式比較

| 方式 | テスト開始 | 完了検知 | 結果取得 | 並列化 | 評価 |
|---|---|---|---|---|---|
| **設定ファイル + コマンドライン**（`terminal64.exe /config:tester.ini`） | ✅ `[Tester]` セクションで EA・銘柄・期間・モデル・最適化・レポート出力先・終了後シャットダウンを指定 | プロセス終了（`ShutdownTerminal=1`）＋レポート/完了マーカー | 最適化: XML（SpreadsheetML）、単一: HTML 系レポート ＋ Telemetry | ポータブル端末を複数用意 | **採用（主方式）** |
| **MetaTrader5 Python API**（`MetaTrader5` パッケージ） | ❌ Strategy Tester を操作する API がない（5.0.6231 の公開関数で確認済み。Windows のみ）[S-MT5PY] | — | — | — | **補助として採用**: `order_calc_profit`/`order_calc_margin` による金額・証拠金換算、 銘柄仕様（契約サイズ・ティック価値・スワップ）の取得、価格履歴取得（レジーム判定・データ検証）、**実運用の約定履歴取得**（Live Monitoring） |
| **GUI 自動化**（pywinauto 等） | △ | △ | △ | ✕ | 不採用（壊れやすい・再現性低） |
| **MQL5 側テレメトリ**（EA に Include 追加、`OnTester`/`FrameAdd`/`OnTesterPass`） | — | ✅ 完了マーカー出力 | ✅ パス別の日次損益・最大含み損・最大ポジション等 | — | **採用（必須部品）** |
| **MQL5 Cloud Network** | ✅ | ✅ | ✅ | 大 | 将来・任意（費用発生、環境差の管理が難しいため最終検証には使わない） |
| **独自 Python バックテスタ**（照合用） | — | — | — | — | 将来・任意（単純戦略で MT5 の結果をクロスチェック） |

**結論**: テスター制御は ini/コマンドライン方式、計測は MQL5 テレメトリ、周辺情報と実運用は Python API、の三本立て。

- P1 では Python API を使わない（実行環境はテレメトリが「テスターが実際に使った値」として出力する）。将来 Python API とテスター起動を併用する場合は、同じポータブル端末を取り合わないよう別インスタンスにするか実行順序を決める (C23)。
- 実機確認（2026-10-09, build 6230）で、ini は ASCII で通り、`Expert=` は `MQL5\Experts` からの相対パス（拡張子なし）で認識されることを確認 [S-HW-P1]。

### 14.2 ini の主要項目と方針

| 項目 | 方針 |
|---|---|
| `Expert`, `ExpertParameters`（.set） | .set は `MQL5\Profiles\Tester\` に置き、ファイル名で指定する。**文字コードは UTF-16LE＋BOM、改行 CRLF**。数値・bool・enum・datetime は `値||開始||ステップ||終了||Y/N` 形式（単一テストでは開始=終了=値、N）、文字列は `name=value`。datetime は Unix 秒の整数。**UTF-8 は ANSI として読まれ非 ASCII が化けるので使わない** (C27) [S-HW-P1] |
| `Symbol`, `Period` | 候補定義から |
| `Model` | 探索: `1 minute OHLC` 可 ／ ⑦以降: **`Every tick based on real ticks` 必須**。異なるモデルの結果は比較しない（`MODEL_MISMATCH`）。実機で確認済みの対応: **4 = リアルティック、1 = 1 分足 OHLC**（0/2/3 は未確認）[S-HW-P1] |
| `ExecutionMode` | 遅延（ms）。⑦以降は現実的遅延 or ランダム遅延 |
| `Optimization` | 0=単一 / 1=完全 / 2=遺伝的。MVP は 1 推奨。**3（全銘柄モード）は使用禁止**（build 6061 で XML が空になるという報告がある (C15) [S-COMM-6061]）。値の対応は実機で確認する（[FINDINGS §3](research/FINDINGS.md)） |
| `OptimizationCriterion` | カスタム（`OnTester` の戻り値）を使う場合はテレメトリ側で定義 |
| `FromDate`, `ToDate` | Holdout Guard 通過後の値のみ。**FromDate は含み、ToDate は含まない**（半開区間）。日時はブローカーサーバ時刻（テスター内の `TimeGMT()` もサーバ時刻を返すため、GMT オフセットはテスター内から得られない）(C28, C31) [S-HW-P1] |
| `ForwardMode` | **0（無効）固定**。期間管理は本基盤が行う（MT5 のフォワードを使うと最適化時点で将来期間の結果を見てしまう） |
| `Deposit`, `Currency`, `Leverage` | Environment から。グリッド系は実運用想定の資金で（証拠金問題を隠さないため） |
| `Report`, `ReplaceReport=1`, `ShutdownTerminal=1` | ジョブ固有パスに出力 |
| `UseLocal=1`, `UseRemote=0`, `UseCloud=0` | 再現性のためローカルエージェントのみ（既定） |

### 14.3 Terminal Pool（並列実行）

- MT5 は 1 端末インスタンスで同時に 1 テストしか実行できないため、**ポータブルモード（`/portable`）の端末インストールを N 個**用意し、プールとして管理。
- 各インスタンスはロックファイルで占有管理。ジョブはキューから空きインスタンスに割当。
- 最適化はインスタンス内でローカルエージェント（CPU コア数）を使うため、**最適化ジョブと単一テストジョブの同時実行数は CPU 総量で制限**（例: 最適化 1 本 or 単一テスト N 本）。
- 全インスタンスで同一ビルド・同一ブローカーサーバ・同一履歴データを使うことを起動時に検証（ビルド番号・銘柄仕様ハッシュの一致確認）。

### 14.4 ライフサイクルと各観点

| 観点 | 設計 |
|---|---|
| **テスト開始** | (1) Holdout Guard 検査 → (2) ini/.set 生成（ジョブ専用ディレクトリ）→ (3) EA の .ex5 ハッシュ照合（意図した版か）→ (4) 出力先の古いファイル削除 → (5) `terminal64.exe /portable /config:…` を起動し PID を記録、状態 RUNNING |
| **開始検知** (C14, C29) | 起動後、一定時間内に**端末ログ**（`<データフォルダ>\logs\YYYYMMDD.log`、UTF-16LE、タブ区切り、日付ごとの追記型）へ `automatic testing started` が出なければ `FAILED_TO_START`。ログは起動直前のファイルサイズ以降だけを読む（日付をまたいだら翌日のファイルも読む）。本文は UI 言語によらず英語 [S-HW-P1] |
| **完了検知** | 単一テスト (C22, C29): ① プロセス終了、② 端末ログの `last test passed with result "successfully finished"`、③ **Telemetry 完了マーカー**、④ 整合性チェック合格、の AND。HTML レポートは解析しない（存在すれば原本として保存するだけ。UI 言語で出力されるため解析に不向き）。終了コードは 0 でも成功の根拠にしない。最適化 (C40, C41): ① プロセス終了、② 端末ログの最適化完了行（文言は実機確認）、③ Frames 回収後の完了マーカー、④ **期待パス数 = Frames の受信数 = XML の行数**、⑤ パラメータの組が期待集合と一致、の AND。欠けたら `PARTIAL` |
| **自動アップデート** (C35, C36) | 起動した端末が LiveUpdate に処理を渡して（端末ログ `LiveUpdate start "...liveupdate\terminal64.exe" /update ... /config:"<ini>"`）すぐ終了し、更新後の端末が同じ ini で自動的に再起動されることがある [S-HW-P1-E2E]。この場合は失敗にせず、再起動された端末の終了まで待って通常どおり判定し、警告 `TERMINAL_UPDATED`（ビルドの推移）を記録する (C35)。また、同じ端末のプロセスが 1 つでも動いている間は判定・後片付けをしない。タイムアウト時はそれらも強制終了する (C36) |
| **結果取得** | Collection が XML/HTML とテレメトリ（共通フォルダ `FILE_COMMON` 配下のジョブ別ファイル）を読み取り、Artifact Store にコピー＆ハッシュ化 |
| **タイムアウト** | ジョブごとに推定時間（過去の 1 パス平均 × パス数 × 安全係数）から上限を設定。加えて**ハートビート**（テスターログ・エージェントログの更新時刻）が一定時間止まったらハング判定。超過時はプロセスツリーごと強制終了（psutil）→ `TIMED_OUT` |
| **異常終了** | 終了コード、レポート欠如、ログ中のエラーパターン（"no history", "cannot load", "stopped", "critical error" 等の分類辞書）で `error_class` を判定。リトライ可否は §15 |
| **再開** | §15 参照（ジョブ単位の冪等再実行、ステージ単位のチェックポイント） |
| **ログ管理** | 端末ログ（`logs/`）、テスターログ（`Tester/logs/`）、エージェントログ（`Tester/Agent-*/logs/`）を**ジョブ実行時間帯で切り出してジョブディレクトリにコピー**。Python 側は構造化ログ（JSON Lines）でジョブ ID を全行に付与 |
| **キャッシュ** | MT5 は最適化結果を `Tester/cache` に保存し、同一条件の再実行をスキップすることがある。再現性確認ジョブでは**キャッシュを削除**して実行、通常ジョブでは本基盤の spec_hash で重複を防ぐ |

### 14.5 再現性のために記録するもの

（C25, C32）P1 では価格履歴のチェックサムは取らず、代わりにテレメトリが観測した最初・最後のティック時刻、ティック数、バー数を簡易の指紋として記録する。また、**MT5 は実行中に新ビルドを自動ダウンロードし、再起動時にビルドが変わりうる**ことを実機で観測したので、ジョブごとに観測ビルドを記録し、ビルドの異なるジョブ同士の再現性比較は「比較不能」として扱う（不一致を失敗にしない）。

端末ビルド番号、ブローカーサーバ名、銘柄仕様スナップショット（契約サイズ・ティック価値・スワップ・手数料・取引時間）、ティックモデル、遅延、初期資金・レバレッジ・通貨、EA の mq5/ex5 ハッシュ、Telemetry バージョン、ini/.set 原本、**価格履歴のチェックサム**（Python API で期間内の M1 足または日足を取得してハッシュ化。ブローカーが履歴を修正した場合に検知するため）。

将来的には、ティックデータを固定した**カスタムシンボル**で実行し、ブローカー履歴の変動から完全に切り離すことを推奨（§21）。

### 14.6 Telemetry Include（MQL5 側ハーネス）の仕様

EA に 1 行 `#include` するだけで以下を提供する共通部品（EA ロジックには触れない）。

| 機能 | 内容 | 用途 |
|---|---|---|
| 日次エクイティ記録 | 日足確定ごとに残高・エクイティ・含み損益・証拠金維持率・ポジション数 | Sharpe 再計算、相関、ポートフォリオ MC |
| 取引記録 | 約定ごとの時刻・方向・ロット・価格・損益・手数料・スワップ・MAE/MFE。P1 は `OnDeinit` で `HistorySelect`→`HistoryDealGet*` により全約定を取得（実機で確認）。**テスト終了時の強制決済は magic=0・comment=`end of test` なので、magic で絞り込まない** (C30) | Bootstrap、コストストレス、利益集中度。整合性チェック: 売買約定の件数 = `STAT_DEALS`、売買約定の Σ(profit+commission+swap+fee) = `STAT_PROFIT` |
| 最大含み損・最大ポジション・最小証拠金維持率 | ティックごとに更新 | グリッド系リスク評価 |
| 最適化パスごとの送信 | `OnTester` で `FrameAdd` により日次損益系列を送信し、端末側の `OnTesterPass` で受信する。**`OnTesterDeinit` で最後に `FrameNext` ループを回して遅れて届いたフレームも回収**してからファイルに出力する [S-MQL5BOOK-FRAME] | DSR の正確化、PBO、StepM/SPA |
| 単一テストの出力 (C13) | **テスターイベント（`OnTesterInit/Pass/Deinit`）と Frames は最適化時のみ動き、単一テストでは使えない** [S-MQL5BOOK-TESTER]。単一テストでは `OnDeinit` で `FILE_COMMON` に直接書き出す [S-MQL5BOOK-FILES]。ファイル名には入力パラメータで渡した**ジョブ ID とパス番号を必ず含める**（複数エージェントの同名ファイル衝突を防ぐ） | 単一テストの取引・日次エクイティ |
| 完了マーカー | パス数・チェックサム・Telemetry バージョン | 完了検知・取りこぼし検知 |
| 固定ロット評価モード | 入力パラメータで強制 | リスク単位正規化 |
| カスタム最適化基準 | `OnTester` の戻り値 | MT5 内の並び替え用（選抜には使わない） |

---

## 15. エラー処理・Resume 設計

### 15.1 エラー分類と対応

| 分類 | 例 | リトライ | 対応 |
|---|---|---|---|
| `TRANSIENT` | 端末クラッシュ、エージェント切断、ファイルロック競合 | ✅ 指数バックオフ、最大 3 回 | 別インスタンスに再割当 |
| `TIMEOUT` | ハング、想定外の長時間 | ✅ 1 回（タイムアウト延長） | 2 回目は `FAILED` + 人間確認 |
| `DETERMINISTIC` | EA ロード失敗、パラメータ不正、ex5 ハッシュ不一致 | ❌ | 即 `FAILED`、設定修正を促す |
| `DATA` | 履歴なし、期間内のデータ欠損、銘柄仕様変化 | ❌ | `FAILED` + `DATA_QUALITY_ISSUE` |
| `PARSE` | レポート形式変化（ビルド更新等） | ❌ | 原本を保存し `QUARANTINED`、パーサ修正後に再取込（再実行不要） |
| `PARTIAL` | 最適化の一部パス欠落 | 🔶 | 欠落パスのみ追加ジョブ化（グリッドを分割して再実行） |
| `GUARD` | Holdout 違反 | ❌ | 拒否・監査ログ。例外ではなく設計通りの挙動 |

### 15.2 Resume の仕組み

- **ジョブ層**: SQLite の `job` テーブルが唯一の状態源。起動時に `RUNNING` なのにプロセスが存在しないジョブを `ORPHANED` に変更し、リトライ方針に従い再キュー。結果ファイルが既に完全（完了マーカーあり）なら再実行せず取込のみ。
- **ステージ層**: `pipeline_execution` に（stage, input_hash）で完了記録。再実行時は同じ input_hash が完了済みならスキップ。
- **候補単位の部分再開**: ステージは候補ごとに Evaluation/Decision を追記するため、途中で落ちても「Evaluation が存在しない候補だけ」を処理すれば再開できる。
- **最適化の粒度**: 1 回の巨大最適化は失敗時の損失が大きいので、パラメータ空間を**チャンク（例: 1 軸で分割）に分けて複数ジョブ化**し、失敗をチャンク単位に局所化。
- **トランザクション**: 1 ジョブの取込（run + metrics + artifact 参照）は 1 トランザクション。Artifact は先に書いてハッシュ名で配置 → DB コミット（逆順にしない）。孤立 Artifact は定期 GC。

---

## 16. MVP と将来拡張の境界

### 16.1 判断基準

「**過学習と破産リスクを減らす効果 ÷ 実装・運用コスト**」が大きいものを MVP にします。

### 16.2 区分表

| 機能 | MVP | Phase 2 | 将来 | 理由 |
|---|---|---|---|---|
| Study 凍結・Holdout Guard・追記型保存・理由コード | ✅ | | | 後から追加不可能な「土台」。最初に入れないと履歴が汚れる |
| ini/コマンドライン実行・Terminal Pool（1〜2 インスタンス） | ✅ | 多インスタンス | | |
| Telemetry（日次エクイティ・取引・最大含み損・完了マーカー） | ✅ | パス別 Frames（P3 に前倒し、C40） | | 自前指標計算と完了検知に必須 |
| MetricEngine（自前再計算） | ✅ | | | |
| 戦略プロファイル・PSR 取引数ゲート | ✅ | | | 一律基準の回避 |
| 近傍ロバストネス（グリッド既存パス）・Robust Score | ✅ | 摂動自動生成 | Sobol 感度 | 費用対効果最大 |
| コストストレス（事後計算） | ✅ | 再シミュレーション | | |
| サブ期間一貫性・利益集中度 | ✅ | | | |
| PSR/MinTRL・DSR（生 N、Frames 導入までは WARN 止まり）・Stationary Bootstrap CI（arch） | ✅ | N_eff（López de Prado & Lewis 2019） | | Haircut Sharpe は DSR と重複するので不採用 (C8) |
| PBO（CSCV） | | ✅ | | Frames が前提 |
| StepM / SPA（arch。RC は SPA に含まれる） | | ✅（StepM 優先） | SPA | 自前実装しない (C3, C4) |
| WFA | | | ✅ | 本基盤の主目的（固定パラメータ）とは別トラック |
| CPCV | | | 保留 | ML 型 EA の導入時のみ。導入時は skfolio を使う (C9) |
| Selector: hard_gate / lexicographic / pareto / region | ✅ | correlation_diverse | weighted | |
| 複数期間検証・OOS（マニフェスト・予測帯） | ✅ | | | |
| Risk MC（取引・日次ブロック） | ✅ | | | |
| Allocator: fixed / dd_constrained / fractional_kelly | ✅ | vol_target | multi_kelly / ERC / HRP | |
| Portfolio: 相関・同時 DD・全体 MC・スケール係数 | | ✅ | 寄与分析・HRP | 単一 EA 段階では不要 |
| Pipeline 自動連結 | | ✅ | | まず各ステージ単独で検証 |
| Live Monitoring・ベイズ更新 | | | ✅ | 実運用開始が前提 |
| レジーム自動判定 | | ✅ | | MVP は手動タグ |
| カスタムシンボルによる完全再現 | | | ✅ | |
| HTML レポート（候補ドシエ） | 簡易 | ✅ | ダッシュボード | |

---

## 17. 推奨ディレクトリ構成

```
mt5-robustlab/
├── pyproject.toml                 # 依存・ツール設定（uv / pip）
├── README.md
├── docs/
│   ├── ARCHITECTURE.md            # 本書
│   ├── adr/                       # 設計判断記録（Architecture Decision Records）
│   └── metrics_definitions.md     # 指標の厳密な定義（バージョン付き）
├── configs/
│   ├── studies/                   # study_*.yaml（研究単位）
│   ├── strategies/                # 戦略ごとのパラメータ空間・プロファイル紐付け
│   ├── profiles/strategy_types.yaml
│   ├── environments/              # ブローカー・端末・コストモデル
│   ├── selection/                 # Selector 設定
│   ├── allocation/                # Allocator・制約
│   └── mt5/terminals.yaml         # Terminal Pool（インスタンスのパス）
├── mql5/
│   ├── Include/RobustLab/Telemetry.mqh
│   ├── Experts/RobustLab/         # 対象 EA（またはシンボリックリンク）
│   └── Scripts/                   # 補助スクリプト（銘柄仕様エクスポート等）
├── src/robustlab/
│   ├── core/                      # 型・ID・理由コード・列挙
│   ├── config/                    # スキーマ・読込・凍結・ハッシュ
│   ├── storage/                   # sqlite リポジトリ・migrations・artifact store
│   ├── metrics/                   # MetricEngine（指標定義のバージョン管理）
│   ├── mt5/                       # ini/set ビルダ・terminal pool・runner・log/report parser
│   ├── generation/
│   ├── collection/
│   ├── filtering/                 # gates/
│   ├── robustness/                # neighborhood, regions, cost_stress, consistency
│   ├── statistics/                # psr, dsr, bootstrap, (pbo, spa)
│   ├── selection/                 # selectors/
│   ├── validation/                # multi_period
│   ├── oos/                       # holdout_guard, manifest, prediction_bands
│   ├── risk/                      # monte_carlo, risk_profile
│   ├── allocation/                # allocators/, lot_sizing
│   ├── portfolio/
│   ├── live/                      # （将来）
│   ├── orchestration/             # pipeline, stage_registry, job_queue
│   ├── reporting/                 # trace, dossier, templates/
│   └── cli/
├── tests/
│   ├── unit/
│   ├── property/                  # hypothesis
│   ├── integration/               # Fake terminal を使う
│   ├── e2e_windows/               # 実 MT5（手動 or 専用マシン）
│   ├── fixtures/                  # 実レポート XML/HTML サンプル・Telemetry サンプル
│   └── synthetic/                 # 合成戦略生成器（ノイズ / 既知エッジ）
└── workspace/                     # .gitignore 対象
    ├── robustlab.sqlite
    ├── artifacts/<hash[0:2]>/<hash>.parquet
    ├── jobs/<job_id>/             # ini, set, レポート, ログのコピー
    └── reports/
```

---

## 18. CLI 設計

コマンド名は `rlab`（案）。**すべてのステージを単独で実行可能**、`--dry-run` で「何が実行されるか」だけ表示、出力は人間向け表＋`--json` で機械可読。

```
# Study 管理
rlab study init     --from configs/studies/macross_eurusd.yaml
rlab study validate <study>            # スキーマ・期間重複・Holdout 衝突を検査
rlab study freeze   <study>            # config_hash 確定、以後変更不可
rlab study show     <study>

# 環境・データ
rlab env check                         # 端末ビルド・銘柄仕様・履歴チェックサムの一致確認
rlab env snapshot   --symbol EURUSD    # 銘柄仕様スナップショット保存（Python API）

# ステージ（個別実行）
rlab generate  <study> [--round R] [--perturb --candidates ...]
rlab run       <study> [--max-parallel N]      # キュー内ジョブを MT5 で実行
rlab collect   <study> [--job J]
rlab filter    <study>
rlab robustness <study>
rlab stats     <study> [--tests psr,dsr]
rlab select    <study> [--selector pareto_v1] [--budget 10]
rlab validate  <study>                          # 複数期間検証
rlab oos plan  <study>                          # マニフェスト・予測帯作成（ロック）
rlab oos run   <study> --confirm-final          # 一度きり
rlab risk      <study>
rlab allocate  <study> --equity 1000000 [--method dd_constrained_fractional_kelly_v1]
rlab portfolio analyze --candidates ... | --plan P

# パイプライン
rlab pipeline run <study> [--from filter] [--until select] [--resume]
rlab pipeline status <study>

# ジョブ運用
rlab jobs list [--status FAILED]
rlab jobs retry <job_id> | rlab jobs cancel <job_id>
rlab jobs logs  <job_id>

# 追跡・レポート
rlab trace   <candidate_id>               # 系譜と全判定（§8.3 の出力）
rlab why-not <candidate_id> --stage select  # 落ちた理由の詳細
rlab report  <study> [--format html]
rlab ledger  <strategy_family>            # 試行回数台帳
```

設計上の約束:
- `oos run` は確認フラグなしでは動かない。2 回目の実行は同一 spec_hash の技術的再実行のみ。
- `select` などを Study 凍結時と異なる Selector で実行すると、警告と共に「追加試行」として台帳に記録。
- 終了コード: 0=成功、1=ユーザーエラー（設定等）、2=実行時エラー、3=ガード拒否。

---

## 19. テスト戦略

### 19.1 層ごとのテスト

| 層 | 方法 | 重点 |
|---|---|---|
| Core / Config | 単体 + プロパティテスト（hypothesis） | ID の決定性（パラメータ順序・数値表現が変わっても同一 ID）、凍結後変更の検出 |
| MetricEngine | 単体（手計算可能な小さな取引列）+ MT5 原値との照合テスト | Sharpe・DD・連敗の定義差の明文化 |
| 統計 | 既知解（論文の数値例）+ シミュレーション | DSR/PSR の数値一致、Bootstrap の被覆率（95% CI が約 95% の確率で真値を含むか） |
| Robustness / Selection / Allocation | 単体 + 合成データ | 孤立ピークを選ばない、プラトー中心を選ぶ、μ_LCB ≤ 0 で配分 0 |
| OOS Guard | 単体（境界・embargo・タイムゾーン）| 1 日でも Holdout に触れるジョブを拒否する |
| MT5 Adapter | **Fake Terminal**（指定時間後にレポートとマーカーを出力、またはハング/クラッシュを模擬するスタブ実行ファイル） | タイムアウト・孤立ジョブ回収・部分結果・再開 |
| Collection | ゴールデンファイル（実際の XML/HTML/Telemetry サンプル） | 端末ビルド更新時の形式変化検知 |
| E2E（Windows 実機） | 小さな EA・短期間で全ステージ | 実際の MT5 との結合、再現性（同一 spec の 2 回実行で結果ハッシュ一致） |

### 19.2 再現性テスト

- 同一 `spec_hash` を 2 回実行し、**正規化した約定データの内容ハッシュ**（列順・行順・数値表現を固定した正規化 CSV の sha256）が一致することを確認（Real ticks・遅延 0 の条件で）。Parquet ファイルのバイト列は書き込みライブラリのバージョンや圧縮設定で変わるので比較に使わない (C26)。ランダム遅延モードでは一致しないため、統計的同等性で判定。端末ビルドが異なる場合は比較不能とする (C32)。単一テストは同条件でも EA が実際に再実行され、キャッシュ削除は不要（実機で確認）[S-HW-P1]。

### 19.3 パイプライン全体の「帰無仮説テスト」（最重要）

- **エッジのない戦略**（ランダムエントリー、または価格系列をシャッフルした合成データ上の戦略）をパイプライン全体に流し、**最終選抜に残る割合＝偽陽性率**を測る。
- 期待: ほぼ 0（例: 5% 未満）。高ければ、ゲート・選抜・統計補正のどこかが甘い。
- 逆に、**既知の小さなエッジを埋め込んだ合成戦略**が、どの程度の確率で残るか（検出力）も測る。
- この 2 つで「パイプライン自体の性能」を定量化できる。Selector や閾値の変更時は必ず再実行（回帰テスト）。

### 19.4 Selector / Allocator のベンチマーク

- 合成データで「真のエッジ」が既知の戦略群を作り、各 Selector の「真に良い戦略を選ぶ率」「OOS 劣化率」を比較。
- Allocator は MC 上で「破産確率」「目標 DD 超過率」「最終資産の中央値」を比較（期待値最大化でなく、DD 制約の遵守を主評価）。

---

## 20. 実装順序

各フェーズの「完了条件」を明確にし、**各モジュールを独立して動作確認してから連結**します。

| フェーズ | 内容 | 完了条件（Definition of Done） |
|---|---|---|
| **P0 土台** | Core（ID・理由コード）、Config（スキーマ・凍結）、Storage（SQLite・Artifact・マイグレーション）。**P1 に必要な最小限（ID・保存・分割定義の固定）は P1 で先に実装する** (C18) | ID 決定性のプロパティテスト通過、凍結後変更が拒否される |
| **P1 単一テスト実行** | MT5 Adapter（ini 生成・起動・開始/完了検知・タイムアウト・ログ収集）、Fake Terminal、Holdout Guard（最小版）、**Telemetry の終了時出力（約定・統計・実行環境・完了マーカー）** (C18)。詳細は [plans/P1_PLAN.md](plans/P1_PLAN.md) | CLI から 1 本の単一テストを実行し、整合性チェックに合格した結果だけが保存される。Holdout 違反が拒否される |
| **P2 Telemetry と指標** | Telemetry.mqh の拡張（日次エクイティ・最大含み損・最大ポジション・固定ロット）、Collection、MetricEngine | 自前指標と MT5 原値の差異が説明可能な範囲。期間別パフォーマンスが出る |
| **P3 最適化と収集** | 最適化ジョブ（完全グリッド・チャンク分割、既定 2,000 パス/ジョブ）、**パス別 Frames（統計＋日次エクイティ、C40）**、XML は照合のみ（C41）、Trial Ledger（C44） | 1,000 パス規模の最適化を取込み、期待パス数と一致 |
| **P4 フィルタ** | 戦略プロファイル、PSR ゲート、全ゲート記録 | `why-not` で全除外理由が表示される |
| **P5 ロバストネス** | 近傍統計・Robust Score・Region・コストストレス（事後）・サブ期間・利益集中 | 合成データで孤立ピークを選ばないことを確認 |
| **P6 統計** | PSR / DSR / Bootstrap CI | 論文数値例と一致 |
| **P7 選択** | hard_gate / lexicographic / pareto / region | 決定性（同入力で同出力）・予算遵守 |
| **P8 検証と OOS** | 複数期間検証、OOS マニフェスト・予測帯・一度きり実行 | 2 回目の OOS 実行が拒否される。`trace` で全経路表示 |
| **P9 リスクと配分** | Risk MC、fixed / dd_constrained / fractional_kelly、ロット変換 | binding_constraint が記録され、min lot 未満は INFEASIBLE |
| **P10 帰無仮説テスト** | §19.3 のパイプライン偽陽性率測定 | 偽陽性率が許容範囲 ← **ここまでが MVP** |
| P11 自動連結 | Pipeline Runner・Resume | 途中停止→再開で結果が一致 |
| P12 Phase 2 統計 | PBO（R `pbo` からの移植）、N_eff、StepM（arch）、摂動自動生成 | PBO が R `pbo` 1.3.5 と同じ入力で数値一致する |
| P13 ポートフォリオ | 相関・同時 DD・全体 MC・スケール係数 | |
| P14 将来 | SPA、再シミュレーション・コストストレス、Live Monitoring、ベイズ更新、HRP | |

> 補足: P10（帰無仮説テスト）を MVP に含めているのは、「この装置がノイズを拾わない」ことを確認しないまま実データの結果を信じるのは危険だからです。

---

## 21. 批判的レビュー

ここからは、この設計自体を疑う視点で問題点を挙げます。設計を良く見せるためではなく、実際に運用したときに足元をすくわれる箇所を先に洗い出すためです。

### 21.1 潜在的な問題点

1. **Validation 期間の「静かな In-Sample 化」**
   ⑦で落とした候補の代わりに⑥の次点を繰り上げる、閾値を調整してもう一度⑦を回す、といった反復を行うと、Validation 期間は実質的に選抜に使われ In-Sample になります。対策: ⑦の再実行回数を台帳に記録し、繰り返すほど DSR の N を増やす。真の OOS は Holdout だけだと割り切る。

2. **Holdout の検出力不足**
   2 年程度の Holdout で年 30 取引の戦略なら 60 取引しかなく、OOS の合否はほぼノイズです。対策: 合否を「予測帯との整合性（反証されないか）」にした（§7.3）のはこのため。さらに、取引頻度の低い戦略は**複数銘柄・複数時間足の Holdout をプール**して検出力を上げる選択肢を持つ（ただし銘柄間相関に注意）。

3. **MT5 の環境依存による再現性の限界**
   ブローカーの履歴修正、端末ビルド更新、ティックデータの差し替えで同じ設定でも結果が変わります。記録だけでは「変わったこと」はわかっても「元に戻す」ことはできません。対策: 最終候補だけでも**カスタムシンボル（固定ティック）での再実行**を将来必須にする。

4. **Real ticks でも約定は楽観的**
   テスターは板の厚さ・約定拒否・リクオート・週明けギャップの約定を十分に再現しません。特にスキャルピングと逆指値多用の戦略は過大評価されやすい。対策: コストストレスを「保険」ではなく「既定の評価条件」として扱い、戦略プロファイルで厳しめのコスト前提を必須にする。

5. **遺伝的最適化の非再現性**
   シードを固定できないため、同じ Study をやり直すと異なる候補集合になります。試行回数（N）の定義も曖昧になります。対策: MVP は完全グリッドを既定にした。遺伝的最適化を使う場合は「評価されたパス数」を N として台帳に記録。

6. **固定ロット評価と実運用（可変ロット）のギャップ**
   評価は固定ロット、運用は口座比率でロットが変わるため、DD の経路が異なります。対策: 配分決定後に「決定したルールでの複利 MC」を追加で回し、DD 分布を再確認するステップを Risk Allocation の最後に置く（Phase 2）。

### 21.2 統計的な落とし穴

1. **DSR の N を過少に数える**のが最も典型的な失敗です。捨てた Study、EA のコード修正前の試行、頭の中で試してやめたアイデアは記録されにくい。台帳は「戦略ファミリー単位」で、疑わしいときは多めに数える方針を明文化しておくべきです。
2. **最適化パスは強く相関している**ため、生の N で DSR を計算すると過大補正、N_eff を楽観的に推定すると過小補正になります。MVP で保守側（生の N）に倒しているのは意図的ですが、その結果「良い戦略まで落とす」可能性があることを認識しておく必要があります。
3. **指標を複数並べること自体が多重検定**です。PF・Sharpe・RF・勝率… を見て「どれかが良い」候補を拾うと、偶然良く見える候補が増えます。Pareto Selector は重みの恣意性を避けますが、使う指標の数を必要最小限（4 つ程度）に絞るべきです。
4. **Bootstrap の独立性仮定**: 取引を独立にリサンプルすると、連敗のクラスター（ボラティリティの塊）が壊れて DD を過小評価します。グリッド/マーチンゲールでは特に致命的です。日次ブロックブートストラップを既定にし、取引リサンプルは補助に留めます。
5. **Sharpe の非正規性**: EA の損益分布は歪度・尖度が大きく（特に損切りの遅い戦略や平均回帰系は負の歪度）、Sharpe が高く見えてもテールで破綻します。PSR/DSR が歪度・尖度を考慮するのはこのため。リスク評価では Sharpe でなく DD 分位点と CVaR を主指標にします。
6. **Kelly の推定誤差感応性**: Kelly 比率は μ の推定誤差にほぼ比例して誤ります。バックテストの μ は選択バイアスで上振れしているので、μ_LCB・分数化・DD 制約の三重の安全装置でも、最終的には DD 制約が効く（binding になる）のが健全です。**Kelly が binding になっている EA があれば、むしろ疑うべきサイン**です。
7. **非定常性**: どれだけ統計的に厳密でも、市場構造の変化（ボラティリティ・レジーム・ブローカー条件）は過去データから保証できません。この基盤が提供するのは「過学習でないことの証拠」であって「将来も機能する保証」ではない、という線引きをレポートに明記すべきです。
8. **相関の不安定性**: ポートフォリオ相関は推定誤差が大きく、危機時に跳ね上がります。相関行列から最適ウェイトを解く手法（平均分散・マルチ Kelly）は MVP から外したのはこのためです。

### 21.3 過剰設計になりうる部分

| 項目 | 懸念 | 推奨 |
|---|---|---|
| プラグインレジストリ（entry points） | 個人/小規模研究では不要 | 単純な辞書登録で十分。必要になってから |
| `run_metrics_ext` の EAV | 使われないまま複雑化 | MVP は作らず、新指標は列追加マイグレーションで対応でも可 |
| CPCV / White RC / Hansen SPA | 実装コストに対し、本基盤（固定パラメータ）での追加情報が少ない | DSR + PBO + 近傍ロバストネスで大半をカバー。SPA は必要性が明確になってから |
| 安定領域のクラスタリング（DBSCAN 等） | パラメータ次元が低ければ連結成分で十分 | MVP は連結成分のみ |
| Terminal Pool の多インスタンス化 | 最初は 1 インスタンスで十分遅くない | P11 以降 |
| Pipeline 全自動化 | 初期は人間が各ステージの結果を見て学ぶ段階 | 手動実行で設計の穴を見つけてから連結 |
| ベイズ更新・SPRT・CUSUM | 実運用前には検証できない | Live 開始後、データが溜まってから |
| 5 種類以上の Selector | 選択肢が多いこと自体が「Selector の過学習」を招く | MVP の既定は pareto + region の 1 種類に絞り、他は比較実験用 |

### 21.4 追加すべき要素

1. **事前登録（Pre-registration）の徹底**: Study 凍結時に、ゲート閾値・Selector・OOS 合格基準をすべて固定（§7.3 を Study 全体に拡大）。
2. **ベンチマーク / 帰無戦略**: 同じエグジット・同じ取引頻度でエントリーだけランダムにした戦略との比較（Entry Randomization）。「エッジがエントリーにあるのか、エグジット/資金管理の副産物か」を判別できます（§10.1 に Phase 2 として追加済み）。
3. **データ品質チェック**: 欠損ティック、異常スプレッド、週末ギャップ、サーバ時刻（GMT オフセット・夏時間）のずれ。時間帯依存の戦略は、ブローカー間でサーバ時刻が違うと結果が大きく変わります。
4. **スワップ・ロールオーバーの時系列変化**: 銘柄仕様スナップショットは「現在」のスワップしか持たないため、長期保有戦略では過去の金利環境とのズレが大きい。スワップ感度（±50% 等）もコストストレスに含めるべきです。
5. **キャパシティ・流動性**: 最終的なロットが大きくなった場合のスリッページ増加（ロット依存コストモデル）。
6. **研究ジャーナル**: 「なぜこの Study を始めたか」「何を見て何を変えたか」を自由記述で Study に紐付ける。台帳に載らない研究者の自由度を後から振り返るため。
7. **キルスイッチ基準の事前定義**: 実運用前に「どの条件で停止するか」（DD コーン 99% 超過など）を allocation_plan に含めて承認する。運用中に感情で判断しないため。
8. **レポートでの不確実性の明示**: 期待値・DD は点推定ではなく必ず区間で表示し、「この候補について我々が知らないこと」（取引数不足、特定レジーム未経験など）を Dossier の固定欄にする。

### 21.5 総評

この設計の強みは、**「何を試し、何を捨て、何を見たか」をすべて記録し、OOS を構造的に守り、資金配分を不確実性と DD 制約で決める**点にあります。これは「収益最大化」ではなく「未知の市場環境への耐性」「過学習耐性」「破産回避」「再現性」「配分の合理性」を優先する目的に合致しています。

一方で、最大のリスクは技術ではなく**運用の規律**です。Validation の反復、Holdout の再利用、試行回数の過少申告は、どれもシステムの外（人間の判断）で起こります。そのため、MVP では統計手法の数を増やすことよりも、**台帳・凍結・監査ログ・帰無仮説テスト**という「規律を仕組みにする部品」を優先しています。ここを最初にしっかり作れば、後から統計手法を差し替えても、過去の研究結果の信頼性が損なわれません。

---

## 付録 A: 用語集

| 用語 | 意味 |
|---|---|
| Discovery 期間 | 最適化（候補生成）に使う唯一の期間 |
| Validation 期間 | パラメータ固定で頑健性を確認する複数期間。選抜の合否には使うが順位付けには使わない |
| Holdout（OOS） | 選抜プロセスから完全に分離し、最後に一度だけ使う期間 |
| Embargo | 期間境界に置く空白。ポジションや指標の持ち越しによる情報漏れ防止 |
| Robust Score | 近傍パラメータを含むスコア分布の下位分位点 |
| Plateau / Region | 合格パラメータが連続して広がる安定領域 |
| Trial Ledger | 多重検定補正のための試行回数台帳 |
| リスク単位 | 固定ロットや R 倍数で正規化した損益の単位 |
| μ_LCB | 期待値の信頼下限（Lower Confidence Bound） |
| binding constraint | 最終的なリスク比率を決めた（最も厳しかった）制約 |

## 付録 B: 推奨ライブラリ

| 用途 | ライブラリ | 備考 |
|---|---|---|
| データ処理 | pandas, numpy, pyarrow | Parquet 入出力 |
| 統計 | scipy, statsmodels | PSR/DSR/MinTRL は自前実装（式が単純）。検算の基準は原著の数値例と R PerformanceAnalytics |
| Bootstrap / ブロック長 / SPA / StepM | **arch 8.0.0**（`StationaryBootstrap`, `optimal_block_length`, `SPA`, `StepM`）。**バージョンを固定**（main と v8.0.0 で SPA の挙動が違う） | Bootstrap は MVP、SPA/StepM は Phase 2〜 |
| PBO | 自前の移植（R `pbo` 1.3.5 と数値一致をテスト） | Phase 2 |
| 実効試行数のクラスタリング・共分散縮小推定 | scikit-learn | Phase 2 / 将来 |
| 多資産 RCK | cvxpy | 将来 |
| 設定・スキーマ | **pydantic** v2, PyYAML（または ruamel.yaml）, tomllib | |
| CLI | **Typer**, Rich（表示） | |
| DB | sqlite3 標準 or SQLAlchemy Core | マイグレーションは簡易自前 or Alembic |
| プロセス管理 | **psutil** | プロセスツリー強制終了・生存確認 |
| ログ | structlog（JSON Lines） | |
| MT5 補助 | MetaTrader5 5.0.6231（公式 Python パッケージ、**Windows のみ**。テスター機能はない） | 銘柄仕様・履歴・`order_calc_profit`/`order_calc_margin`・実運用約定 |
| レポート | Jinja2 + Plotly（HTML） | |
| テスト | pytest, **hypothesis** | |
| 高速化（任意） | numba | 大規模 MC 時 |
