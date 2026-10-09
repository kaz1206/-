# P1 実装計画: MT5 単一バックテストの安全な実行と再現可能な保存

- 状態: **実装完了・実機 E2E 待ち（2026-10-09）**。手順は `docs/runbooks/P1_E2E.md`。実機確認（`verification/p1/RECORD.md`）完了。設計変更 E1・F1〜F8・D1〜D8 は承認済みで、設計書に反映済み（C18〜C34）。**本文と §15 の「確定事項」が食い違う場合は §15 を優先する**
- 作成日: 2026-10-08
- 前提として確認した文書: `docs/ARCHITECTURE.md` v0.2、`docs/research/FINDINGS.md`、`docs/research/SOURCES.md`、`docs/DESIGN_CHANGELOG.md`、`CLAUDE.md`
- リポジトリの現状: コードは 0 行。ドキュメントのみ（上記 5 ファイル＋`README.md`）。再利用できる既存実装はない。

## P1 の目的（これだけ）

> Python から MT5 の単一バックテストを**安全に**実行し、その結果を**再現可能な形で**保存できる。

- 「安全に」の中身:
  - Holdout 期間を使わない。
  - 途中で止まっても、壊れた結果を成功として保存しない。
  - 認証情報を保存しない。
- 「再現可能に」の中身:
  - 何を（EA の版・パラメータ）、どの条件で（期間・テスター設定・端末ビルド）実行し、何が出たか（約定一覧・MT5 統計・原本ファイル）を、ハッシュ付きで保存する。
  - 同じ条件で再実行して結果を比較できる。

---

## 1. 設計書・リポジトリの確認結果と、P1 に必要な機能の特定

設計書のうち P1 に必要な部分だけを抜き出した。設計書 §20 では P0（土台）が P1 の前にあるが、P0 全体（Study 凍結・汎用マイグレーション等）は P1 の目的に不要なので、**P1 に必要な最小限の P0 だけを取り込む**。

| 設計書の要素 | P1 で使う範囲 | 使わない範囲 |
|---|---|---|
| §8.1 ID 設計 | `strategy_version_id`, `candidate_id`, `run_id`, `spec_hash`, `job_id` | evaluation / decision の ID |
| §5.2 データモデル | strategy_version, candidate, run, job, artifact, holdout_access_log, mt5_reported_metrics | study, data_period, optimization_round, evaluation, decision, run_metrics, period_performance, region, trial_ledger 等 |
| §5.1 Artifact Store | 内容ハッシュ名のファイル保存、約定一覧の Parquet | 日次損益・パス別行列 |
| §7.2 Holdout Guard | 期間交差チェック、監査ログ、分割定義の固定（最小版） | OOS マニフェスト、解錠手順、汚染伝播、使用回数集計 |
| §14 MT5 実行管理 | ini/.set 生成、1 端末での起動、開始検知、完了検知、タイムアウト、ログ回収 | Terminal Pool、最適化、キャッシュ管理、ハートビート |
| §14.6 Telemetry | **終了時のみ**の出力（約定一覧・MT5 統計・実行環境メタ・完了マーカー） | ティックごとの計測（最大含み損等）、日次エクイティ、Frames |
| §15 エラー処理 | エラー分類と、失敗を成功扱いしないこと | 自動リトライ、Resume、孤立ジョブの回収 |
| §18 CLI | `backtest run` / `backtest show` / `jobs list` | 他の全コマンド |
| §19 テスト | 単体テスト、Fake Terminal による結合テスト、実機 E2E（手動） | 帰無仮説テスト等 |

---

## 2. 方式の確定

### 2.1 Strategy Tester の起動方式（確定）

**`terminal64.exe /portable /config:<job.ini>` による単一テスト（`Optimization=0`）。終了は `[Tester] ShutdownTerminal=1` に任せる。**

- 根拠:
  - MetaTrader5 Python API（5.0.6231）にはテスター機能がない [S-MT5PY]。
  - `/config` と `[Tester]` による起動は公式ヘルプに記載がある [S-MT5START]。
  - `[Tester]` の `ShutdownTerminal` は、build 4230 で追加された `[StartUp]` 版（スクリプト専用）とは別物 [S-MT5B4230]。
- `/portable` にする理由: データフォルダをインストール先に固定でき、ログ・`.set`・レポートの場所が決定的になるため。
- 端末は **1 インスタンスのみ**。起動前に、同じ実行ファイルの端末が既に動いていたら実行を拒否する（既に起動中の端末に `/config` を渡したときの挙動が未確認のため。実機確認 W10）。
- **P1 では MetaTrader5 Python パッケージを使わない**:
  - 端末ビルド・銘柄仕様・口座通貨は、EA 側の Telemetry が「テスターが実際に使った値」として出力する。これは Python API で事前に取る値より再現性の記録として正確。
  - これにより P1 の Python 側は Windows 専用の依存を持たず、Linux で単体テストできる。
  - Python API は、資金配分（`order_calc_*`）が必要になる P9 以降で導入する。

### 2.2 結果の取得・保存形式（確定）

| 取得物 | 取得方法 | 保存形式 | 正本か |
|---|---|---|---|
| 約定一覧（deals） | EA の `OnDeinit` で `HistorySelect` → `HistoryDealGet*` を全件走査し、`FILE_COMMON` に CSV で出力 | 原本 CSV（artifact）＋正規化した Parquet（artifact） | **正本** |
| MT5 統計 | EA が `TesterStatistics()` の主要値を出力（JSON） | 原本 JSON（artifact）＋ `mt5_reported_metrics` テーブル | 照合用 |
| 実行環境メタ | EA が端末ビルド・口座通貨・サーバ名・初期資金・レバレッジ・銘柄仕様（契約サイズ・ティック値・ティックサイズ・桁数・スプレッド設定・スワップ・ロット刻み）・実際の開始/終了時刻を出力（JSON） | 原本 JSON（artifact）＋ job テーブルの列 | **正本**（観測された環境） |
| 完了マーカー | EA が最後に出力（job_id、telemetry_version、約定件数、各ファイルの行数） | 原本 JSON（artifact） | 完了判定に使う |
| MT5 レポート | `[Tester] Report=` で出力される HTML | 原本ファイル（artifact）。**P1 では解析しない** | 監査用 |
| ini / .set | Python が生成 | 原本ファイル（artifact） | 再実行用 |
| ログ | 端末ログ・テスターログ・エージェントログを、ジョブの実行時間帯について回収 | 原本ファイル（artifact） | 監査用 |

- 正本を Telemetry（EA 側）にする理由: 単一テストのレポートは HTML/XLSX で、構造化データは最適化の XML のみ [S-MT5REPORT]。また、テスターイベントと Frames は最適化時しか動かない [S-MQL5BOOK-TESTER]（C13）。
- **整合性チェック**（壊れた結果を成功にしないための中核）:
  1. 完了マーカーの job_id が実行中のジョブと一致する。
  2. マーカーに書かれた約定件数・行数が、CSV の実際の行数と一致する。
  3. 約定一覧の Σ(profit + commission + swap) が、MT5 統計の純損益と許容誤差（口座通貨 0.01）以内で一致する。
  4. 観測された銘柄・時間足・期間が、依頼内容と一致する。
- 保存先:
  - SQLite: `workspace/robustlab.sqlite`
  - 原本: `workspace/artifacts/<sha256 先頭 2 桁>/<sha256>`（内容ハッシュ名・書き込み後は変更しない）
  - 作業ディレクトリ: `workspace/jobs/<job_id>/`（実行中の一時置き場。正本は artifacts 側）

### 2.3 ID の扱い（P1 での決定）

| ID | P1 での生成規則 | 設計書（§8.1）との差分 |
|---|---|---|
| `strategy_name` | 戦略 YAML に書く人間向けの名前（例: `RL_SmokeTest`） | 「Strategy ID」の役割。ハッシュではない |
| `strategy_version_id` | `sv_` + sha256(正規化 JSON{ex5_sha256, mq5_sha256 または null, telemetry_version}) 先頭 16 桁 | **ソース非公開の EA（mq5 なし）を許容する**: mq5_sha256 = null、`source_available=false` を記録。設計書に追記が必要（後述 D3） |
| `candidate_id` | `cd_` + sha256(正規化 JSON{strategy_version_id, symbol, timeframe, params}) 先頭 16 桁 | **`RL_` で始まる Telemetry 用の入力（job_id 等）は params から除外**する（含めると毎回 ID が変わる）。設計書に追記が必要（D4） |
| `run_id` | `rn_` + sha256(正規化 JSON{candidate_id, from_date, to_date, tester_settings_hash, cost_scenario="BASE", source="SINGLE_TEST"}) 先頭 16 桁 | 設計書の `period_id` と `env_id` は P1 には存在しない（Study と期間テーブルがない）。日付と「**依頼したテスター設定**」のハッシュで代用する（D2） |
| `tester_settings_hash` | sha256(正規化 JSON{model, execution_delay, deposit, currency, leverage, terminal_path}) | 設計書の `env_id` は端末ビルドを含むが、ビルドは**実行後にしか分からない**。そこで「依頼した設定（ID に含める）」と「観測した環境（job に記録し、不一致を警告）」に分ける（D2） |
| `spec_hash` | sha256(正規化 BacktestJobSpec 全体) | 設計書どおり |
| `job_id` | `jb_` + UTC タイムスタンプ（ミリ秒）＋ランダム 8 桁 16 進（時系列でソート可能。外部ライブラリなし） | 設計書の「ULID」を簡易化。1 つの run に対する 1 回の実行 = 1 job（attempt） |

**パラメータ正規化**（設計書 §8.1 の具体化）:
- キーをソートする。
- int は int のまま。double は `repr` ではなく、有効桁 15 桁の文字列に統一する。
- bool は true/false、enum は整数値。
- **戦略 YAML に宣言した入力名の集合と完全一致しなければエラー**（入力の追加・欠落・綴り違いを検出する）。

**冪等性**: 同じ `run_id` に成功済みの job がある場合、既存の結果を表示して終了する（再実行しない）。`--rerun` を付けたときだけ新しい job（attempt）を作る（再現性の確認用）。

### 2.4 OOS 封印の P1 最小実装（確定）

P1 には OOS ステージがないので、**Holdout に触れる実行は無条件に拒否する**（解錠手段そのものを実装しない）。

1. `configs/data_partition.yaml` に Holdout 期間（＋embargo の日数）を定義する。期間は**ブローカーサーバ時刻の日付**で書く（MT5 の FromDate/ToDate と同じ基準）。
2. **分割定義の固定**: 初回実行時に分割定義のハッシュを DB に登録する。以後ハッシュが変わったら、すべての実行を拒否する（「後から Holdout をずらして使う」ことを防ぐ。Study 凍結の最小版）。変更が本当に必要なら、新しい workspace を作る運用にする。
3. **交差チェック**: 依頼された期間 [from, to] が、Holdout 期間を embargo 分だけ広げた区間と 1 日でも重なれば拒否する。境界は閉区間として保守的に扱う（MT5 の ToDate が当日を含むかは W12 で確認）。
4. **監査ログ**: 許可・拒否の両方を `holdout_access_log` に記録する（依頼のハッシュ・期間・分割定義のハッシュ・判定・理由）。
5. **拒否はコマンドライン引数で回避できない**（バイパス用のフラグを用意しない）。終了コードは 3（§18）。
6. 拒否されたジョブについては、ini も .set も生成しない（MT5 を起動しない）。

> 技術的に止められないもの: 人間が MT5 の GUI で Holdout 期間を直接テストすること。これは設計書 §7 のとおり監査ログと運用ルールで扱い、P1 の範囲外とする。

---

## 3. 実行フロー

```
rlab backtest run configs/requests/<request>.yaml [--rerun]
 │
 ├─ 1. 設定読込・検証（pydantic）
 │     request.yaml / strategies/<name>.yaml / terminal.yaml / data_partition.yaml
 ├─ 2. 事前チェック（失敗時は PREFLIGHT_FAILED。MT5 は起動しない）
 │     terminal64.exe が存在する / 同じ端末が起動していない / ex5 がデータフォルダ内にある
 │     / 戦略 YAML の入力名と params が完全一致 / 認証情報を含む設定キーがない
 ├─ 3. Holdout Guard（拒否時は GUARD_REJECTED、終了コード 3。監査ログに記録）
 ├─ 4. ID 計算と登録（strategy_version / candidate / run は「なければ追加」）
 ├─ 5. 冪等チェック（成功済みで --rerun なし → 既存の結果を表示して終了）
 ├─ 6. job 作成（状態 PREPARING）、作業ディレクトリ作成
 ├─ 7. .set と ini を生成
 │     .set: params ＋ RL_JobId（データフォルダの MQL5/Profiles/Tester/ に配置。W3）
 │     ini : [Tester] のみ。Login/Password を含む [Common] は生成しない
 ├─ 8. 出力先の掃除（Common/Files 内の当該 job_id のファイル、レポート出力先）
 ├─ 9. 起動: terminal64.exe /portable /config:<ini>、PID を記録、状態 RUNNING
 ├─10. 開始検知: 制限時間内にテスターログへ開始行が出なければ、プロセスツリーを終了 → FAILED_TO_START（C14）
 ├─11. 終了待ち: 全体のタイムアウトを超えたらプロセスツリーを終了 → TIMED_OUT
 ├─12. 回収: 完了マーカー・CSV・JSON・レポート・ログを作業ディレクトリへ集める
 │     マーカーがない → TELEMETRY_MISSING（端末異常終了を含む）
 ├─13. 検証: 整合性チェック 4 項目（§2.2）。失敗 → QUARANTINED（原本は保存する）
 ├─14. 保存: 原本を artifacts に格納 → 約定を Parquet 化 → DB を 1 トランザクションで更新（状態 SUCCEEDED）
 │     （artifact を先に書き、DB コミットを後にする。§15.2）
 └─15. 要約を表示: run_id / job_id / 約定件数 / 純損益（MT5 値・自前の合計値）/ 観測ビルド / 警告
```

## 4. 入力

| ファイル | 内容 | 例 |
|---|---|---|
| `configs/terminal.yaml` | `terminal_path`（ポータブル版 terminal64.exe）、`common_files_dir`（Common/Files の実パス）、`start_timeout_sec`、`run_timeout_sec` | Windows 実機ごとに作る（リポジトリには `.example` のみ置く） |
| `configs/data_partition.yaml` | Holdout 期間、embargo 日数、タイムゾーンの注記 | `holdout: {from: 2024-07-01, to: 2026-06-30}`, `embargo_days: 10` |
| `configs/strategies/<name>.yaml` | `strategy_name`、ex5 のパス（`MQL5/Experts` からの相対パス）、mq5 のパス（任意）、`telemetry_version`、入力名と型の一覧 | `RL_SmokeTest` |
| `configs/requests/<name>.yaml` | strategy、symbol、timeframe、from_date、to_date、params、tester 設定（model、execution_delay、deposit、currency、leverage） | `smoketest_eurusd_h1_2019.yaml` |

## 5. 出力

| 出力 | 場所 |
|---|---|
| DB レコード | `strategy_version`, `candidate`, `run`, `job`, `artifact`, `job_artifact`, `mt5_reported_metrics`, `holdout_access_log`, `partition_registry`, `schema_version` |
| 原本ファイル | `workspace/artifacts/` |
| 正規化済み約定 | `workspace/artifacts/` 内の Parquet（スキーマのバージョン付き） |
| 標準出力 | 要約（人間向け）。`--json` で機械可読 |
| 終了コード | 0 = 成功（または成功済み）、1 = 設定・入力エラー、2 = 実行時エラー（起動失敗・タイムアウト・検証失敗）、3 = Guard 拒否 |

## 6. エラー処理

| 状態 / エラー分類 | 発生条件 | 処理 | 自動リトライ |
|---|---|---|---|
| `CONFIG_INVALID` | YAML のスキーマ違反、入力名の不一致、認証キーを含む | MT5 を起動しない。終了コード 1 | なし |
| `PREFLIGHT_FAILED` | 端末・ex5 がない、端末が既に起動中 | 同上 | なし |
| `GUARD_REJECTED` | Holdout と交差、分割定義が変更されている | 監査ログに記録。終了コード 3 | なし |
| `FAILED_TO_START` | 開始行がテスターログに出ない | プロセスツリーを終了し、ログを回収。終了コード 2 | なし |
| `TIMED_OUT` | 全体のタイムアウト超過 | 同上 | なし |
| `TELEMETRY_MISSING` | プロセスは終了したが完了マーカーがない（異常終了・キャッシュ使用の疑い） | ログを回収。終了コード 2 | なし |
| `QUARANTINED` | 整合性チェックの失敗、または解析失敗 | **原本を保存したうえで成功扱いにしない**。終了コード 2 | なし |
| `SUCCEEDED` | 全チェックに合格 | — | — |

- P1 では**自動リトライも Resume も実装しない**（設計書 §15 の簡易版）。失敗したジョブは状態を残し、人間が原因を見て `--rerun` する。
- Python プロセス自体が途中で落ちた場合、job は `RUNNING` のまま残る。次回起動時に「`RUNNING` なのに PID が生きていない job」を `ABANDONED` に変更する処理だけ入れる（数行で済み、誤解を防ぐため）。

## 7. ファイル計画

### 7.1 新規作成するファイル

| ファイル | 責務 |
|---|---|
| `pyproject.toml` | パッケージ定義（`robustlab`、Python ≥3.12）。依存: pydantic、PyYAML、typer、psutil、pyarrow。開発用: pytest、hypothesis。ロックファイルで固定（ツールは実装時に公式ドキュメントで確認） |
| `.gitignore` | `workspace/`、`configs/terminal.yaml`（端末ごとの設定） |
| `src/robustlab/__init__.py` | バージョン |
| `src/robustlab/core/ids.py` | 正規化 JSON、sha256、各 ID の生成、job_id 生成、パラメータ正規化 |
| `src/robustlab/core/models.py` | pydantic モデル（TerminalConfig、DataPartition、StrategySpec、BacktestRequest、BacktestJobSpec、JobStatus、ErrorClass） |
| `src/robustlab/config/loader.py` | YAML の読込と検証、設定ハッシュの計算 |
| `src/robustlab/oos/holdout_guard.py` | 分割定義の登録・変更検知、期間交差の判定、監査ログの記録 |
| `src/robustlab/storage/db.py` | SQLite の DDL（P1 で使うテーブルのみ）、`schema_version`、追記用関数、job 状態の更新（job だけは更新可） |
| `src/robustlab/storage/artifacts.py` | 内容ハッシュ名での保存・重複排除・読み出し |
| `src/robustlab/mt5/ini_builder.py` | `[Tester]` の ini と `.set` の生成（文字コード・改行は W3 の結果に合わせる）、認証キーの混入防止 |
| `src/robustlab/mt5/terminal.py` | 事前チェック（実行中の検出）、起動、開始検知、タイムアウト、プロセスツリーの終了、ログ回収（psutil） |
| `src/robustlab/mt5/telemetry_reader.py` | マーカー・CSV・JSON の読込、整合性チェック 4 項目、約定の Parquet 化 |
| `src/robustlab/single_backtest.py` | §3 の実行フローの本体（上記モジュールを順に呼ぶだけ） |
| `src/robustlab/cli/main.py` | `rlab backtest run`、`rlab backtest show <run_id>`、`rlab jobs list` |
| `mql5/Include/RobustLab/Telemetry.mqh` | 終了時出力のみ: `RL_TelemetryOnInit()`（開始情報の保持）と `RL_TelemetryOnDeinit()`（約定 CSV・統計 JSON・環境メタ JSON・完了マーカーの書き出し）。ファイル名に `RL_JobId` を含める |
| `mql5/Experts/RobustLab/RL_SmokeTest.mq5` | 検証専用の小さな決定的 EA（固定ロットの単純な移動平均クロス）。Telemetry の動作確認用で、戦略としての意味はない |
| `configs/terminal.example.yaml` | 端末設定の雛形 |
| `configs/data_partition.yaml` | Holdout の定義 |
| `configs/strategies/rl_smoketest.yaml` | スモークテスト EA の定義 |
| `configs/requests/smoketest_eurusd_h1.yaml` | Holdout に触れない期間での実行依頼の例 |
| `tests/unit/test_ids.py` | ID の決定性（キー順・数値表現が変わっても同じ ID になる。hypothesis）、`RL_` 入力の除外 |
| `tests/unit/test_holdout_guard.py` | 境界日・embargo・分割定義の変更検知・監査ログ |
| `tests/unit/test_ini_builder.py` | 生成物のゴールデンファイル比較、認証キーの拒否 |
| `tests/unit/test_telemetry_reader.py` | 正常系、行数不一致、損益合計の不一致、job_id の不一致 |
| `tests/unit/test_storage.py` | 内容ハッシュ保存、研究結果テーブルが追記のみであること |
| `tests/integration/fake_terminal.py` | 偽の端末: ini を読み、指定の振る舞いをする（正常出力 / 開始しない / ハング / マーカーなしで終了 / 不整合な出力） |
| `tests/integration/test_single_backtest.py` | Fake Terminal を使った §3 の全分岐の結合テスト（Linux で実行可能） |
| `tests/fixtures/telemetry/` | 正常系・異常系の Telemetry サンプル。**実機で得た本物の出力を W 項目の確認時に追加する** |
| `docs/runbooks/P1_WINDOWS_CHECKS.md` | 実機確認（§8 の W1〜W14）の手順と記録欄 |

### 7.2 変更するファイル

| ファイル | 変更内容 | 時期 |
|---|---|---|
| `docs/ARCHITECTURE.md` | §9 の D1〜D5 を反映（§20 の P0/P1/P2 の境界、§5.2 の env の分離、§8.1 の mq5 なし・`RL_` 入力の除外、§14.4 の単一テストの完了条件） | 計画承認後、実装の最初のコミット |
| `docs/DESIGN_CHANGELOG.md` | D1〜D8 を C18 以降の番号で追記 | 同上 |
| `docs/research/FINDINGS.md` | §3 チェックリストに P1 固有の項目を追加し、実機確認の結果を記入 | 実機確認時 |
| `CLAUDE.md` | ビルド・テストのコマンドを追記（実在するコマンドだけ） | 実装後 |
| `README.md` | P1 の使い方へのリンク | 実装後 |

---

## 8. Windows 実機で事前確認が必要な項目

公式ページが本環境から読めなかった項目を含む。**W1〜W9 はコードを書く前（または ini_builder / terminal / Telemetry に着手する前）に確認する**。結果は `docs/research/FINDINGS.md` §3 と `docs/runbooks/P1_WINDOWS_CHECKS.md` に記録する。

| # | 確認項目 | P1 で影響する箇所 | 優先 |
|---|---|---|---|
| W1 | `/portable /config:` ＋ `[Tester]`（`Optimization=0`）で単一テストが始まり、`ShutdownTerminal=1` で端末が終了するか。終了コードの値 | terminal.py | 最優先 |
| W2 | `Expert=` のパス形式（`MQL5/Experts` からの相対パスか、拡張子の要否） | ini_builder.py | 最優先 |
| W3 | `ExpertParameters=` の `.set` の置き場所（`MQL5/Profiles/Tester` か）、文字コード（UTF-16LE か）、改行コード、単一テスト用の書式 | ini_builder.py | 最優先 |
| W4 | `Model` の値の対応（4 = リアルティックか）、`ExecutionMode` の値 | models.py | 最優先 |
| W5 | `Report=` の基準パス、拡張子の自動付与、`ReplaceReport` の効果 | terminal.py | 高 |
| W6 | ポータブル時のテスターログの場所、**ログの文字コード（UTF-16LE か）**、テスト開始・終了を示す行の文言 | terminal.py（開始検知） | 高 |
| W7 | テスター（ローカルエージェント）から `FILE_COMMON` に書けるか、Common/Files の実パス | Telemetry.mqh / terminal.yaml | 最優先 |
| W8 | `OnDeinit` 内で `HistorySelect` / `HistoryDealGet*` が全約定を返すか、`TesterStatistics()` が `OnDeinit` で有効か（無効なら `OnTester` で取得して保持する） | Telemetry.mqh | 最優先 |
| W9 | テスター内での `TerminalInfoInteger(TERMINAL_BUILD)`、`AccountInfoString(ACCOUNT_SERVER/CURRENCY)`、`SymbolInfo*` の値（テスターが使った値か） | Telemetry.mqh | 高 |
| W10 | 同じ端末が既に起動中に `/config` で起動したときの挙動 | terminal.py（事前チェック） | 中 |
| W11 | 単一テストのキャッシュ: 同じ条件で再実行したとき、EA が実際に再実行されるか（Telemetry が出るか） | `--rerun` の意味、TELEMETRY_MISSING | 高 |
| W12 | `FromDate`/`ToDate` の包含関係（ToDate 当日を含むか）とタイムゾーン（サーバ時刻か） | holdout_guard.py | 高 |
| W13 | 再現性: 同じ依頼を 2 回実行して、約定一覧（Parquet）のハッシュが一致するか（リアルティック・遅延 0） | 完了条件 | 完了時 |
| W14 | ログイン状態の要件: ログインなしでもテストできるか、初回の履歴ダウンロードの挙動（**ini にパスワードを書かない運用が成り立つか**） | 事前チェック、セキュリティ | 高 |

---

## 9. 設計書と P1 方針の矛盾・不整合（承認後に設計書へ反映）

| # | 箇所 | 矛盾・不整合 | P1 での解決 |
|---|---|---|---|
| D1 | §20（P0→P1→P2 の順序） | P1 の完了条件「結果を正しく取得・保存」には Telemetry が必要だが、設計書では Telemetry は P2。また P0 全体（Study 凍結等）は P1 に不要 | P0 のうち ID・保存・最小限の分割定義の固定だけを P1 に取り込む。**Telemetry の「終了時出力」を P1 に前倒し**し、ティック単位の計測・日次エクイティは P2 に残す |
| D2 | §5.2 `environment.env_id` に `terminal_build` を含む、`run_id` に `period_id`/`env_id` を使う | ビルドは実行後にしか分からないので、実行前に決まる ID に入れられない。P1 には Study も期間テーブルもない | 「依頼したテスター設定」（ID に含める）と「観測した環境」（job に記録し、不一致を警告）に分ける。`run_id` は日付と設定ハッシュで作る |
| D3 | §8.1 `strategy_version_id` = hash(ex5 + mq5 + telemetry) | ソース非公開の EA を扱えない | mq5 は任意。ない場合は null を入れ、`source_available=false` を記録する |
| D4 | §8.1 パラメータ正規化（全パラメータを含める） | Telemetry 用の入力（`RL_JobId`）を含めると、毎回 candidate_id が変わる | `RL_` で始まる入力は「ハーネス用」として除外する |
| D5 | §14.4 完了検知「レポートファイルが存在し解析可能」 | P1 では HTML レポートを解析しない（正本は Telemetry） | 単一テストの完了 = プロセス終了 ＋ 完了マーカー ＋ 整合性チェック合格。レポートは「存在すれば保存する」だけ |
| D6 | §14.4（1 つの端末に Python API と /config を併用する想定） | 同じポータブル端末を Python API とテスター起動で取り合う可能性がある | P1 では Python API を使わない（§2.1）。将来使うときは別インスタンスか、実行順序を決める |
| D7 | §7.2 は Guard 拒否を「例外で拒否」、§15.1 は「例外ではなく設計通りの挙動」 | 表現の不一致 | P1 では「正常な判定結果としての拒否」（監査ログ ＋ 終了コード 3）に統一する。§7.2 の文言を修正する |
| D8 | §14.5 価格履歴のチェックサム（Python API で取得） | P1 では Python API を使わない | P1 では記録しない（P2 以降）。代わりに EA が観測した最初と最後のティック時刻・バー数をメタに記録し、簡易の指紋とする |

## 10. 公式仕様・原著研究との矛盾チェック

| 対象 | 確認結果 |
|---|---|
| 単一テストで Frames を使わないこと | [S-MQL5BOOK-TESTER] と整合（C13）。矛盾なし |
| 正本を Telemetry とし、HTML を解析しないこと | [S-MT5REPORT]（単一テストは HTML/XLSX）と整合。矛盾なし |
| `[Tester] ShutdownTerminal` | [S-MT5START], [S-MT5B4230] と整合。`[StartUp]` 版とは混同しない |
| `FILE_COMMON` を使い、ファイル名に job_id を入れること | [S-MQL5BOOK-FILES] と整合。衝突を防ぐ |
| `Model` の値の対応 | **未確定**（第三者情報のみ）。W4 で確認するまで、コード上は列挙名で持ち、数値への変換表を 1 か所に集める |
| 統計手法（DSR、PBO 等） | P1 では使わないので影響なし |
| ARCHITECTURE §14.2 の `ExecutionMode` の「ランダム遅延」 | 値は未確認（W4）。P1 は遅延 0 のみを扱う |
| P1 で使うライブラリ | pydantic 2.13、PyYAML、typer 0.27、psutil 7.2、pyarrow 25.0 [S-PYPI-VERS]。PyYAML は今回の調査対象外だったので、着手時に PyPI で現行版を確認し SOURCES に追加する |

**原著研究・公式仕様と矛盾する設計は、P1 の範囲では見つからなかった。** 不整合は設計書の内部（D1〜D8）に限られる。

---

## 11. P1 で実装しないもの（明示）

- 最適化、大量の候補生成、Frames、パス別データ
- Selection Engine、フィルタ・ゲート、Decision、理由コードの体系
- PSR / DSR / PBO / StepM / Bootstrap / Monte Carlo
- Kelly、資金配分、Portfolio、Live 注文・Live 監視
- MetaTrader5 Python パッケージの利用（環境スナップショット、`order_calc_*`）
- 価格履歴のチェックサム、カスタムシンボル
- ティックごとの計測（最大含み損・最大ポジション・証拠金維持率）、日次エクイティ
- MetricEngine（自前の指標再計算）。P1 で計算するのは整合性チェック用の損益合計だけ
- HTML レポートの解析
- Terminal Pool、並列実行、ハートビート監視、キャッシュ削除の自動化
- 自動リトライ、Resume（`ABANDONED` への変更だけは行う）
- Study 凍結の全機能、OOS マニフェスト、Holdout の解錠
- 複数期間・複数銘柄・複数 Battlefield の自動処理
- MetaEditor による自動コンパイル（P1 では人間が事前にコンパイルし、ハッシュだけ取る）
- GUI、HTML レポート生成、`trace` / `why-not`

---

## 12. テスト方法

| 層 | 環境 | 内容 |
|---|---|---|
| 単体 | Linux（CI）/ Windows | ID の決定性（hypothesis）、Guard の境界、ini/.set のゴールデン比較、Telemetry 読込と整合性チェック、追記のみの保存 |
| 結合 | Linux（CI）/ Windows | Fake Terminal（Python 製の偽 `terminal64`）で §3 の全分岐: 成功、開始しない、ハング（タイムアウト）、マーカーなしで終了、行数不一致、損益不一致、Guard 拒否、冪等（2 回目は再実行しない）、`--rerun` |
| 実機 E2E | Windows ＋ MT5（手動） | `RL_SmokeTest` を EURUSD H1・Holdout 外の 1 年で実行する。①成功し保存される、②`--rerun` で**正規化した約定データの内容ハッシュ**が一致する（W13、E1）、③Holdout と交差する依頼が拒否され、監査ログに残る、④実行中に端末を強制終了すると SUCCEEDED にならない |
| 実機で得たデータの固定 | — | 実機で得た本物の Telemetry 出力とログを `tests/fixtures/` に追加し、以後は Linux の回帰テストで使う |

## 13. 完了条件（Definition of Done）

1. Linux 上で `pytest` の単体テスト・結合テストがすべて通る。
2. W1〜W12 と W14 の確認結果が `docs/research/FINDINGS.md` に記録され、コードがその結果に合わせてある。
3. Windows 実機で `rlab backtest run` により `RL_SmokeTest` の単一テストが 1 本成功し、DB と artifacts に保存される。
4. 同じ依頼の `--rerun` で、**正規化した約定データの内容ハッシュ**（列順・行順・数値表現を固定した正規化 CSV の sha256）が一致する（W13、E1。一致しない場合は原因を特定して記録する）。Parquet のバイト列は比較に使わない。
5. Holdout と交差する依頼が MT5 を起動せずに拒否され、`holdout_access_log` に残る。分割定義を書き換えると、以後の実行が拒否される。
6. 端末の強制終了・開始失敗・タイムアウトのいずれも、SUCCEEDED として保存されない。
7. 生成された ini・artifacts のどこにも、ログイン ID・パスワードが含まれない。
8. `rlab backtest show <run_id>` で、EA の版・パラメータ・期間・テスター設定・観測環境・原本ファイルのハッシュを確認できる。
9. 設計書の D1〜D8 の修正と、CHANGELOG（C18 以降）が反映されている。

## 14. 着手順序（承認後）

1. 実機確認 W1〜W9（MQL5 側の最小 EA を手で動かして確認する。これは確認作業であって、本実装ではない）
2. 設計書の D1〜D8 反映、CHANGELOG 追記
3. `core/ids.py`、`core/models.py`、`config/loader.py` ＋ 単体テスト
4. `oos/holdout_guard.py`、`storage/*` ＋ 単体テスト
5. `mt5/ini_builder.py`、`mt5/telemetry_reader.py` ＋ 単体テスト（W の結果を反映）
6. `Telemetry.mqh`、`RL_SmokeTest.mq5`（実機で出力を確認し、fixtures に保存）
7. `mt5/terminal.py`、`single_backtest.py`、Fake Terminal ＋ 結合テスト
8. `cli/main.py`
9. 実機 E2E（W13 を含む）、完了条件の確認

---

## 15. 実機確認後の確定事項（F1〜F8、2026-10-09 承認）

本文の未確定部分は以下で確定する。

| ID | 本文の箇所 | 確定内容 |
|---|---|---|
| F1 | §2.2、§7.1 `ini_builder.py` | `.set` は UTF-16LE＋BOM・CRLF。数値・bool・enum・datetime は `名前=値\|\|値\|\|刻み\|\|値\|\|N`（刻みは int/datetime/enum/bool で 1 または 0、double で 0.1 等の任意値でよい。単一テストでは使われない）、文字列は `名前=値`。datetime は Unix 秒の整数 |
| F2 | §2.2 整合性チェック 4、§2.3 | run の期間は半開区間 `[from_date, to_date)`（サーバ時刻）。最初のティックが from_date から `coverage_tolerance_days`（既定 7）日より後、または最後のティックが to_date の同日数より前なら `QUARANTINED`（`DATA_COVERAGE`） |
| F3 | §3 手順 10・11 | 開始 = 端末ログ（`<データフォルダ>/logs/YYYYMMDD.log`、UTF-16LE）に `automatic testing started`。成功 = 同ログの `last test passed with result "successfully finished"` ＋ プロセス終了 ＋ 完了マーカー ＋ 整合性チェック。起動直前のファイルサイズ以降だけを読む |
| F4 | §2.2 整合性チェック 3 | 売買約定（type 0/1）の件数 = `STAT_DEALS`、かつ売買約定の Σ(profit+commission+swap+fee) = `STAT_PROFIT`（許容 0.01）。magic で絞り込まない |
| F5 | §2.2 実行環境メタ | spread は観測値として別記録し、銘柄仕様のハッシュに含めない。時刻はすべてサーバ時刻 |
| F6 | D2、§13-4 | `--rerun` の比較で前回とビルドが違えば「比較不能（ビルド変更）」。`backtest show` で job ごとのビルドを並べる |
| F7 | §4 `terminal.yaml` | `run_timeout_sec` 既定 3600、`start_timeout_sec` 既定 300 |
| F8 | §5 | ログ artifacts には口座番号と接続元 IP が含まれる。CLI はログ本文を表示しない（パスとハッシュのみ） |

## 16. 実装メモ（計画からの細部の補足）

計画の範囲内で、実装時に決めた細部です（仕様の変更ではありません）。

| 項目 | 内容 | 理由 |
|---|---|---|
| `INTERNAL_ERROR` 状態 | job 作成後に rlab 内部で想定外の例外が起きた場合（起動失敗など）、job を `INTERNAL_ERROR` で閉じる | job を `RUNNING` のまま残さず、成功扱いにもしないため（§6 の表に 1 行追加） |
| 後片付け | job が作った端末側のファイル（`.set`、Common の `RL_<job>_*`、`RL_<job>_report*`）は、artifacts に保存した後に削除する。ログは端末側に残す（追記型の共有ファイルのため、該当区間だけを artifacts に保存） | 端末フォルダ・共有フォルダにファイルを溜めないため |
| ログの保存単位 | 起動直前のサイズ以降に追記された区間だけを `log_segment` として保存する | 他の実行の行を混ぜないため（F3） |
| `tester_settings_hash` | 計画どおり `terminal_path` を含める | §2.3 |
| 期間外ティックの扱い | 最初のティックが FromDate より前、または最後のティックが ToDate 以降なら `QUARANTINED`（`PERIOD_SEMANTICS`） | 実機で確認した半開区間（F2）と違う挙動を検出するため |
