# P3 実装計画: MT5 最適化の実行と全パスの取込（Trial Ledger）

- 状態: **承認済み（2026-10-10、J1〜J6 すべて推奨どおり。設計変更 C40〜C45）**。実機確認の準備完了（`verification/p3/`）→ 結果を待って実装
- 作成日: 2026-10-10
- 前提: P1・P2 完了（`docs/plans/P1_PLAN.md` §17、`docs/plans/P2_PLAN.md` §13）。設計書 `docs/ARCHITECTURE.md` v0.3 の §20「P3 最適化と収集」
- 進め方: P1・P2 と同じく **計画 → 承認 → 実機確認 → 実装 → 実機 E2E**。実機で確かめるまで仕様を確定しない

## P3 の目的（これだけ）

> Discovery 期間で MT5 の**完全グリッド最適化**を安全に実行し、**全パス（捨てるパスも含む）**を候補・実行結果として取り込み、**試行回数を台帳（`trial_ledger`）に正確に残す**。取りこぼしは「期待パス数 ≠ 実パス数」で必ず検知する。

P3 では「どの候補が良いか」は判定しない（選抜・統計は P4 以降）。P3 の価値は、後の DSR・PBO・StepM の**分母（試行回数）と入力（全パスの成績）**を、欠けなく・再現可能に揃えることにある。

---

## 1. 調査結果（計画の根拠）

### 1.1 確認できたこと・できなかったこと

クラウド環境から `metatrader5.com` / `mql5.com` は名前解決できず（2026-10-10）、公式ページを直接読めなかった。検索結果の要約から分かった範囲を確認レベル B/C で記録し、**仕様の確定は実機確認（§5）に回す**。

| 事項 | 内容 | 確認レベル |
|---|---|---|
| 最適化の種類 | 「低速な完全アルゴリズム（全組合せ）」「高速な遺伝的アルゴリズム」「気配値表示の全銘柄」がある | B（公式ヘルプの検索要約）[S-MT5OPTTYPES] |
| ini の `Optimization` の値 | 0 = 無効（単一テスト）は実機で確認済み。1 = 完全、2 = 遺伝的、3 = 全銘柄 は未確認 | 0 は A [S-HW-P1]、他は V |
| 最適化レポートの形式 | XML（SpreadsheetML）| B [S-MT5REPORT] |
| XML の列 | `Pass` 列＋入力パラメータ列＋指標列（Result, Profit, Gross Profit, …）。**`Result` は最適化基準の値**であって残高ではない。列名は UI 言語に依存する可能性がある | C [S-FORUM-OPTXML] |
| パス数の多い最適化 | UI ではパスが欠けて見えても XML には全行ある、という報告。逆に「XML が空」（`Optimization=3`）の報告もある | C [S-FORUM-OPTXML][S-COMM-6061] |
| Frames | `OnTester` 内で `FrameAdd` → 端末側の `OnTesterPass` で受信 → `OnTesterDeinit` で遅れて届いた分も回収。**テスターイベントは最適化時のみ動く** | B [S-MQL5BOOK-TESTER][S-MQL5BOOK-FRAME]、build 619 で導入 [S-MT5B619] |
| `FrameAdd` の 1 フレームのサイズ上限 | 公式の記載を確認できず | V（FINDINGS §3 の未確認項目） |
| 最適化キャッシュ | 最適化結果は `Tester/cache` の `.opt` に保存され、同条件の再実行で使われることがある | B [S-MT5OPTTYPES の検索要約、MQL5 Book] |
| MQL5 Cloud Network | 有料の外部エージェント。使うと費用が発生し、実行環境も本基盤の管理外になる | 一般知識。ini での無効化キーは V |

### 1.2 採否を決めるべき論点（CLAUDE.md の 5 観点で評価）

#### (a) パスごとの成績をどこから取るか

| 観点 | XML レポートを正本にする | **Telemetry Frames を正本にする（推奨）** |
|---|---|---|
| 何を測るか | MT5 が計算した指標（定義が不透明な値を含む） | P2 と同じ自前の定義（MetricEngine）。日次損益も取れる |
| 依存する仮定 | 列名・書式が UI 言語やビルドで変わらない | Frames が全パス分届く（`OnTesterDeinit` で回収・件数照合で保証） |
| 今回への適合 | 単一テストで HTML を解析しない方針（C22）と矛盾する | P2 の方針（研究で使う値は自前で計算）と一貫する。DSR の V[SR] と PBO に必要な**パス別の日次損益**が同時に揃う |
| 他手法との重複 | — | XML は「件数・パラメータ・純損益の照合」に限定して併用（重複ではなく相互検証） |
| 実装コスト | 低〜中（XML 解析のみ） | 中（MQL5 側に Frames の送受信を追加） |

→ **Frames を正本、XML を照合用**にする（J1・J2）。設計書では Frames を Phase 2（P12）に置いていたが、P3 で全パスを取り込む時点で日次損益まで揃えておくと、後から最適化をやり直す必要がなくなる。**設計変更なので承認が必要**。

#### (b) 最適化アルゴリズム

完全グリッドのみ（`Optimization=1`）。遺伝的（2）は乱数シードを制御できず、試行の集合と回数 N が再現しない（設計書 §19 の批判的レビュー 5）。全銘柄（3）は XML が空になる報告があり使わない（C15）。設定の検証で 1 以外を拒否する。

#### (c) 既存実装の再利用

- XML 解析: 標準ライブラリ `xml.etree.ElementTree`（SpreadsheetML は単純な XML）。新しい依存は追加しない。外部由来の XML を読むので、`defusedxml` の要否を実装前に評価する（エンティティ展開攻撃の対策。ファイルは自分の端末が出力したものなので優先度は低い）。
- 日次行列の保存: P2 と同じ Parquet ＋ 正規化 CSV の内容ハッシュ（E1）。
- 指標計算: P2 の MetricEngine を、パスの日次系列と `TesterStatistics` の値に対して再利用する（約定一覧はパスごとには送らない。§3.3）。

---

## 2. P3 の範囲

### 2.1 実装するもの

| # | 項目 | 内容 |
|---|---|---|
| 1 | Study 設定（YAML） | 戦略、銘柄・時間足、Discovery 期間、パラメータ空間（各パラメータの `start/step/stop`、固定値）、モデル（Real ticks 既定）、初期資金。`extra=forbid` で未知のキーを拒否 |
| 2 | グリッド展開とチャンク分割 | パラメータ空間を決定的に展開し、**期待パス数 = 各軸の値の数の積**を計算。1 ジョブあたりの上限（既定 2,000 パス）を超えたら、最初の最適化軸で分割して複数ジョブにする（J4） |
| 3 | 最適化用 `.set` / ini | `.set` は最適化対象を `値\|\|start\|\|step\|\|stop\|\|Y`、固定値を `\|\|N`（UTF-16LE＋BOM、C27）。ini は `Optimization=1`、`ForwardMode=0`、`ShutdownTerminal=1`、Report 出力先を指定。クラウド・リモートエージェントは使わない（J6） |
| 4 | Holdout Guard | 最適化ジョブは用途ステージ `DISCOVERY`。期間が Discovery の外（特に Holdout）に触れたら拒否（終了コード 3） |
| 5 | Telemetry v3（MQL5） | 各パスの `OnTester` で、`TesterStatistics` の値・EA 追跡値（P2 の v2 と同じ）・**日次エクイティ終値の配列**を `FrameAdd`。端末側の `OnTesterPass` / `OnTesterDeinit` で受信し、`FILE_COMMON` にジョブ別ファイルとして書き出す。全フレーム回収後に完了マーカー（受信パス数を含む） |
| 6 | 完了判定 | ① 端末プロセス終了（LiveUpdate の引継ぎを含む、C35/C36）、② 端末ログの最適化完了行（文言は実機確認）、③ 完了マーカー、④ **Frames の受信パス数 = XML の行数 = 期待パス数**、⑤ パラメータの組が期待集合と一致。どれかが欠けたら SUCCEEDED にしない。欠けたパスがあれば `PARTIAL` とし、欠けたパスの一覧を記録 |
| 7 | 取込 | パスごとに `candidate`（origin = `OPTIMIZATION`、ID は P1 と同じ内容ハッシュ）、`run`（source = `OPT_PASS`）、`run_metrics`（MetricEngine、パス向けの指標だけ）、`metric_check`（Frames の値と XML の純損益・取引数の照合）。日次行列（日 × パス）を Artifact に保存 |
| 8 | Trial Ledger | 最適化ラウンドごとに、**実行を依頼したパス数**（欠けたパス・取引ゼロのパスも含む）を `trial_ledger` に追記。集計単位は戦略ファミリー（J5） |
| 9 | キャッシュ対策 | 最適化ジョブの前に、rlab が管理する端末の `Tester/cache` を退避・削除する（J3）。キャッシュから結果が返るとパスが実行されず Frames が届かないおそれがあるため |
| 10 | CLI | `rlab optimize run <study.yaml>`、`rlab optimize show <round_id>`、`rlab trials show <strategy_family>`。既存の `jobs list` に最適化ジョブも表示 |
| 11 | 偽の端末の拡張 | `tests/integration/fake_terminal.py` に最適化モード（全パス成功・一部欠落・XML 空・Frames 欠落・キャッシュ返却）を追加し、Linux で全テストが動くようにする |

### 2.2 P3 で実装しないもの

- 遺伝的最適化・全銘柄モード・フォワードモード
- 候補の選抜、初期フィルタ、パラメータ安定性（P4 以降）
- DSR・PBO・StepM・N_eff の計算（P3 は入力を揃えるだけ）
- 摂動候補の追加実行
- 複数端末での並列実行（P1 と同じく 1 度に 1 ジョブ）
- パスごとの約定一覧の保存（データ量が大きい。必要な候補は単一テストで再実行して取る）

---

## 3. 仕様案

### 3.1 Study 設定の例（`configs/studies/rl_smoketest_grid.yaml`）

```yaml
study_id: st_smoketest_grid_v1
strategy: configs/strategies/rl_smoketest.yaml
symbol: EURUSD
timeframe: H1
period: {from: 2021-01-04, to: 2023-01-02}   # Discovery の中だけ（Guard で検査）
model: REAL_TICKS
deposit: 10000
param_space:
  FastPeriod: {start: 5, step: 1, stop: 40}     # 36 値
  SlowPeriod: {start: 20, step: 5, stop: 160}   # 29 値  → 期待 1,044 パス
fixed:
  Lots: 0.01
max_passes_per_job: 2000
```

- `step` が 0 以下、`start > stop`、刻みで割り切れない範囲は設定エラー（終了コード 1）。
- 浮動小数の軸は P1 の正規化（`format(v, '.15g')`）で値を作り、MT5 側の値と文字列で突き合わせる。
- 「FastPeriod ≥ SlowPeriod」のような無効な組合せも**グリッドから除かない**（MT5 は全組合せを回すため。除くと期待パス数がずれる）。EA が取引しないだけで、1 試行として台帳に数える。

### 3.2 ジョブとラウンド

- 1 つの Study 実行 = 1 つの `optimization_round`。チャンクに分けた場合も、ラウンドは 1 つで、ジョブが複数になる。
- ラウンドの `expected_passes` は全チャンクの合計。`actual_passes` はチャンクごとの照合結果の合計。
- 同じ Study（内容ハッシュが同じ）を再実行したら、P1 と同じく冪等（既存結果を返す）。`--rerun` で再現性確認として実行し、パスごとの純損益・取引数・日次系列ハッシュを比較する（`REPRO_*`）。

### 3.3 Frames の中身（Telemetry v3）

1 パスにつき 1 フレーム。

| 部分 | 中身 |
|---|---|
| 識別 | ジョブ ID、パス番号、入力パラメータ（`FrameInputs` でも取れるが、照合のため自分でも送る） |
| 統計 | P2 の `stats.json` と同じ `TesterStatistics` の値 |
| EA 追跡値 | P2 の v2 と同じ（エクイティ最高値・最大 DD、最大含み損、最大ポジション数・ロット、最小証拠金維持率、ロスカット件数） |
| 日次系列 | 日付と日終値エクイティの配列（2 年で約 520 日 × 16 バイト ≈ 8 KB） |

端末側の出力（`FILE_COMMON`、ファイル名にジョブ ID を含める）:
- `RL_<job>_passes.csv`: パスごとに 1 行（統計・追跡値・パラメータ）
- `RL_<job>_daily.csv`: 縦持ち（パス番号, 日付, 日終値エクイティ）
- `RL_<job>_done.json`: 受信パス数、重複したパス番号、書込み完了フラグ

取込側で縦持ちを「日 × パス」の行列にして Parquet に保存し、正規化 CSV のハッシュを記録する。

### 3.4 パスごとの指標

約定一覧がないので、P2 の指標のうち次のものだけを計算する（`metric_def_version` は `m2-pass` として区別する案。実装時に確定）。

- `TesterStatistics` 由来（MT5 の値をそのまま使う。P2 で定義一致を確認済みのもの）: 純損益、取引数、勝ち/負け取引数、総利益/総損失、PF、期待利得、連勝・連敗、残高 DD
- EA 追跡値: エクイティ最大 DD、リカバリーファクター、最大含み損、破産フラグなど
- 日次系列から自前で計算: 日次平均・標準偏差・日次シャープ・歪度・尖度

P2 で「MT5 と一致を確認済み」の指標に限って `TesterStatistics` の値を使う。これは「研究で使う値は自前で計算」という方針の例外になるため、`docs/metrics_definitions.md` に明記する（取引指標は約定データからの自前計算と MT5 が一致することを P2 で確かめた、が根拠）。

### 3.5 データモデルの追加（追記のみ、SCHEMA_VERSION 3）

設計書 §6 の定義に合わせる。

| テーブル | 主な列 | 備考 |
|---|---|---|
| `optimization_round` | round_id, study_id, strategy_version_id, symbol, timeframe, period, param_space_json, algorithm = `FULL_GRID`, expected_passes, created_at | 追記のみ |
| `round_job` | round_id, job_id, chunk_index, expected_passes, actual_passes, missing_json | ラウンドとジョブの対応 |
| `trial_ledger` | ledger_id, study_id, strategy_family, round_id, n_trials_raw, n_trials_effective(NULL), method = `RAW`, created_at | 追記のみ。N_eff は P12 |
| `run`（既存の拡張） | source 列に `OPT_PASS` を追加 | 既存の行は `SINGLE_TEST` |

いずれも UPDATE・DELETE を禁止するトリガーを付ける（`job` 表は従来どおり状態更新あり）。

---

## 4. 安全性

| リスク | 対策 |
|---|---|
| クラウドエージェント（有料）が使われる | ini で無効化（キー名は実機確認）。実機確認で「エージェント一覧にローカルしか使われていない」ことを確かめる。確認できるまで E2E を行わない |
| 実口座への発注 | P1 と同じく EA は `MQL_TESTER` 以外では動かない。最適化の端末側インスタンス（`MQL_FRAME_MODE`）では取引関数を呼ばない |
| Holdout の混入 | ジョブ生成時に Guard を通す。Study の期間が Holdout ＋ embargo に触れたら拒否 |
| CPU の占有 | 最適化はローカルエージェントが全コアを使う。実行中は PC が重くなることを runbook に書く。エージェント数の制限は実機確認後に検討 |
| ログの機密情報 | P1 と同じ（口座番号・IP。CLI で本文を表示しない、コミットしない） |

---

## 5. 実機確認（あなたの操作が必要なもの）

P1 と同じく、**検証用 EA とスクリプトを `verification/p3/` に分けて**用意する（P3 本体からは参照しない）。小さなグリッド（2 パラメータ × 3〜4 値 ≈ 12 パス、M1 OHLC、3 か月）で、1 回あたり数分で終わるようにする。

| # | 確認すること | 優先 | なぜ必要か |
|---|---|---|---|
| X1 | `Optimization=1` で ini から完全グリッド最適化が始まり、`ShutdownTerminal=1` で終了する | 最優先 | P3 の前提 |
| X2 | 最適化の開始・完了を示す端末ログの文言 | 最優先 | 完了判定 ② |
| X3 | XML レポートの保存先・ファイル名・文字コード・列名（UI 言語の影響）・`Pass` 列・行数 | 最優先 | 照合の方法を決める |
| X4 | Frames が全パス分届くか（`OnTesterPass` と `OnTesterDeinit` での回収）、端末側から `FILE_COMMON` に書けるか | 最優先 | Telemetry v3 の前提 |
| X5 | 日次系列（3 年分 ≈ 800 日）を 1 フレームで送れるか | 高 | フレームサイズの上限が不明 |
| X6 | 同じ最適化を 2 回実行したとき、2 回目がキャッシュから返るか。そのとき `OnTester` と Frames は動くか。`Tester/cache` を消すとどうなるか | 高 | J3 の根拠 |
| X7 | クラウド・リモートエージェントを ini で無効にできるか、実際にローカルだけが使われたか | 最優先（安全） | 費用と再現性 |
| X8 | 最適化のあるパスと、同じパラメータの単一テストの結果が一致するか（Real ticks、遅延 0） | 高 | 候補を後で単一テストし直すときの前提（FINDINGS §3 の未確認項目） |
| X9 | `.set` の `\|\|Y` で最適化軸、`\|\|N` で固定（`RL_JobId` などの文字列入力を含む）になるか | 高 | `.set` の書式 |
| X10 | 1 パスあたりの所要時間とエージェント数 | 中 | チャンク上限の既定値 |

あなたの操作は P1 と同じく「ファイルのコピー → コンパイル → スクリプト実行 → 結果フォルダの zip を送る」だけにする。コマンドは 1 つずつ分けて示す。

---

## 6. 変更・新規作成するファイル（予定）

| 種類 | ファイル |
|---|---|
| 検証（P3 本体と分離） | `verification/p3/mql5/RL_Verify_P3.mq5`、`verification/p3/scripts/verify_p3.ps1`、`verification/p3/README.md`、`verification/p3/RECORD.md` |
| MQL5 | `mql5/Include/RobustLab/Telemetry.mqh`（v3: Frames の送受信を追加。単一テストの v2 の動作は変えない） |
| Python | `src/robustlab/core/models.py`（Study 設定）、`src/robustlab/generation/grid.py`（展開・チャンク）、`src/robustlab/mt5/ini_builder.py`（最適化用）、`src/robustlab/mt5/opt_report.py`（XML 照合）、`src/robustlab/mt5/frames_reader.py`、`src/robustlab/optimization.py`（実行フロー）、`src/robustlab/storage/db.py`（v3）、`src/robustlab/cli/main.py` |
| 設定 | `configs/studies/rl_smoketest_grid.yaml` |
| テスト | 単体（グリッド展開・チャンク・`.set`・XML・Frames 読込・照合・台帳）、結合（偽の端末の最適化モード）、DB 移行 v2→v3 |
| 文書 | `docs/ARCHITECTURE.md`、`docs/DESIGN_CHANGELOG.md`、`docs/research/SOURCES.md`、`docs/research/FINDINGS.md`、`docs/metrics_definitions.md`、`docs/runbooks/P3_E2E.md`、`CLAUDE.md` |

---

## 7. 完了条件

1. 約 1,000 パスの完全グリッド最適化を `rlab optimize run` で実行し、**期待パス数 = Frames の受信数 = XML の行数**で SUCCEEDED になる。
2. 全パスが `candidate` / `run` / `run_metrics` に入り、日次行列（日 × パス）が Artifact に保存される。
3. Frames の純損益・取引数が XML の値と全パスで一致する（照合表で確認）。
4. `trial_ledger` に依頼したパス数がそのまま記録される（取引ゼロのパスも含む）。
5. Holdout に触れる Study は終了コード 3 で拒否される。
6. 1 パスでも欠けたら SUCCEEDED にならず、欠けたパスの一覧が出る（偽の端末で確認）。
7. 同じ Study の `--rerun` で、全パスの結果が一致する（`REPRO_MATCH`）。
8. Linux で `uv run pytest` が全件通る。

---

## 8. 着手順序（承認後）

1. 設計変更（J1〜J6）を設計書・変更履歴に反映
2. 検証用 EA とスクリプト（`verification/p3/`）を作成 → あなたに実機確認をお願いする
3. 結果を記録し、仕様を確定（必要なら設計変更案を再提示）
4. 本体の実装（Python → MQL5 → 偽の端末 → テスト）
5. 実機 E2E（約 1,000 パス）

---

## 9. 承認をお願いしたい判断（要約）

| ID | 判断 | 推奨 | 理由 |
|---|---|---|---|
| J1 | Telemetry Frames（パス別の統計と日次エクイティ）を P12 から **P3 に前倒し** | 前倒し | 全パスの取込と同時に日次損益まで揃えれば、DSR・PBO のために最適化をやり直さずに済む |
| J2 | パスごとの成績の正本は **Frames**、XML は件数・パラメータ・純損益の照合だけに使う | Frames 正本 | XML の列名は UI 言語に依存しうる。単一テストで HTML を解析しない方針（C22）と一貫 |
| J3 | 最適化ジョブの前に、rlab 管理の端末の `Tester/cache` を退避・削除する | 削除する（実機確認 X6 の結果で最終決定） | キャッシュ返却でパスが実行されず Frames が欠けるのを防ぐ |
| J4 | 1 ジョブの上限は既定 2,000 パス、超えたら最初の最適化軸で分割 | 2,000 | 失敗をチャンク単位に局所化（設計書 §14.8）。値は X10 の結果で見直す |
| J5 | `trial_ledger` は戦略ファミリー単位で、**依頼したパス数**（欠けたパス・取引ゼロのパス・捨てた Study も含む）を数える | そのとおり | 多重検定の分母を過小にしない（保守側） |
| J6 | クラウド・リモートエージェントは使用禁止。ini で無効化し、実機で確認できるまで E2E を行わない | 禁止 | 費用の発生と、実行環境が管理外になることを防ぐ |
