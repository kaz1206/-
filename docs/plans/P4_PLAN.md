# P4 実装計画: 初期フィルタ（戦略プロファイル・PSR ゲート・全ゲート記録）

- 状態: **計画（承認待ち）**
- 作成日: 2026-10-11
- 前提: P3 完了（`docs/plans/P3_PLAN.md` §13）。設計書 `docs/ARCHITECTURE.md` §3.3（Initial Filtering）、§5.4（理由コード）、§9.6（戦略プロファイル）、§10.2（PSR/MinTRL）、§20「P4 フィルタ」
- 進め方: **計画 → 承認 → 実装 → 実機での動作確認**。P4 は MT5 を使わない（保存済みのデータだけを読む）ので、実機確認フェーズはない

## P4 の目的（これだけ）

> 最適化ラウンドの全パスに、**明らかに不適格な候補を安価なルールで落とす「足切り」**をかけ、**すべてのゲートの結果と理由を記録**する。`rlab why-not <candidate_id>` で、どのゲートで、どの値が、どの閾値に対して落ちたかを全部表示できる状態にする。

P4 はランキングも選抜もしない（§3.3「やらないこと」）。P4 を通っても「良い候補」ではなく、「次の評価（P5 ロバストネス・P6 統計）に進める資格がある」という意味にとどまる。

---

## 1. 調査結果（計画の根拠）

### 1.1 PSR・MinTRL の一次情報（2026-10-11 に確認）

| 事項 | 内容 | 確認レベル |
|---|---|---|
| PSR の式 | PSR(SR*) = Φ( (ŜR − SR*)·√(n−1) / √(1 − γ̂₃·ŜR + (γ̂₄−1)/4·ŜR²) )。ŜR は年率化しない観測頻度の Sharpe（標本標準偏差）、γ̂₃ は歪度、γ̂₄ は**超過でない**尖度（moment 法） | **A**（R PerformanceAnalytics 2.1.0 `ProbSharpeRatio.R` のソースで確認。設計書の式 [S-PSR] と一致） |
| MinTRL の式 | MinTRL = 1 + (1 − γ̂₃·ŜR + (γ̂₄−1)/4·ŜR²)·(z_p / (ŜR − SR*))² | **A**（同 `MinTRL.R`） |
| R の既定値 | **`ignore_kurtosis = TRUE`（尖度を 3 に固定）**、`ignore_skewness = FALSE`。ソースのコメントに「高次モーメントはデータを多く必要とし、短い系列では尖度を使うべきでない」 | A |
| 原著の数値例 | R の例 `sr = 2/√12, refSR = 1/√12, sk = −0.72, kr = 5.78, n = 59`（月次）。原著の掲載値そのものは今回確認できず | 例の入力は A、原著の出力値は未確認 |

→ 設計どおり**原著の式（尖度を含む）**を使う。日次データで 1 年あたり約 260 点あり、R の注意書き（短い系列）には当たりにくい。尖度が大きいほど PSR は下がる（＝保守側）。R と比べるテストでは `ignore_kurtosis = FALSE` に相当する値と比べる（M1）。

### 1.2 既存実装の評価（CLAUDE.md の 5 観点）

| 候補 | 評価 | 採否 |
|---|---|---|
| R PerformanceAnalytics | 式の参照実装として最良（CRAN、2026-04 更新）。ただし Python から呼ぶには R が必要 | **検算の基準**（テストオラクル）に使う。依存には入れない |
| Python の既存パッケージ（quantstats など） | 式は数行で、パッケージごとに尖度の定義（超過かどうか）や既定値がばらつく。依存を増やす価値がない | 不採用 |
| 自前実装 | 式は数行。scipy の `norm.cdf` / `norm.ppf` を使う。P2 で尖度（`fisher=False`）・歪度（`bias=True`）はすでに R の moment 法と同じ定義で計算している | **採用** |

### 1.3 P3 のデータで分かっていること

1,044 パスの純損益の中央値は −20.56、黒字は 32%（P3_PLAN §12）。動作確認用の EA にエッジはないはずなので、**P4 を通るパスはほぼない**と予想する。これはそのまま「フィルタが機能しているか」の最初の確認になる。

ただし、**PSR は多重検定を補正しない**。エッジのない 1,044 パスに PSR ≥ 0.95 をかけると、偶然でも約 5%（50 前後）が通りうる。これを割り引くのは P6 の DSR（試行回数 N = 1,044 を使う）の役割で、P4 では補正しない。この役割分担を設計どおり守る。

---

## 2. P4 の範囲

### 2.1 実装するもの

| # | 項目 | 内容 |
|---|---|---|
| 1 | 戦略プロファイル | `configs/strategy_profiles.yaml` に戦略タイプ別の閾値を定義（§9.6）。戦略 YAML に `profile:` を追加（例: `RL_SmokeTest` は `intraday`）。プロファイルの内容はハッシュで固定し、判定ごとに記録 |
| 2 | 統計部品 | `stats/psr.py`: PSR・MinTRL（原著の式）。標本長・Sharpe・歪度・尖度を入力にとる純粋関数 |
| 3 | ゲート | 下の §3 の各ゲート。**全ゲートを必ず評価**し、最初の不合格で止めない |
| 4 | 記録 | 設計書の `evaluation`（ゲートごとの計算値）と `decision`（候補ごとの PASS/FAIL と全理由コード）を追記のみで保存。`reason_code` カタログ表 |
| 5 | CLI | `rlab filter run <round_id>`、`rlab filter show <filter_id>`（理由コード別の件数）、`rlab why-not <candidate_id>`（ゲートごとの値・閾値・合否） |
| 6 | ストレージの改善（M4） | P3 で説明した日次データの 3 重保存を解消（§5） |

### 2.2 P4 で実装しないもの

- ロバストネス（近傍・島）→ P5
- DSR・ブートストラップ → P6
- 選抜 → P7
- 単一テストの結果へのフィルタ（P8 の複数期間検証でまとめて扱う）
- DD の上限による足切り（M2。理由は §3.2）

---

## 3. ゲートの仕様案

### 3.1 ゲート一覧（`intraday` プロファイルの既定値）

| ゲート | 理由コード | 判定（不合格の条件） | 使う値（P3 の p1 指標） | 既定の閾値 |
|---|---|---|---|---|
| 破産 | `BANKRUPTCY` | エクイティの最小値 ≤ 0 | `bankrupt` | — |
| ロスカット | `STOP_OUT` | ロスカット約定が 1 件以上 | `stop_out_deals` | — |
| 証拠金維持率 | `MARGIN_LEVEL_LOW` | 最小証拠金維持率 < 閾値 | `min_margin_level` | 200% |
| 同時ポジション数 | `MAX_POSITIONS_EXCEEDED` | 最大同時ポジション数 > 閾値 | `max_positions` | プロファイルごと（`intraday` は設定なし＝評価のみ） |
| 取引数の絶対下限 | `INSUFFICIENT_TRADES` | 取引数 < 下限（安全装置。本体は PSR） | `trades` | 30 |
| 日次データ量 | `INSUFFICIENT_DATA` | 日数 < 下限（歪度・尖度の推定に必要） | `days` | 60 |
| 期待値 | `NEGATIVE_EXPECTANCY` | 純損益 ≤ 0 | `net_profit` | — |
| PSR | `LOW_PSR` | PSR(SR* = 0) < 閾値。分散 0（取引なし）も不合格 | `days`、`daily_sharpe`、`daily_skewness`、`daily_kurtosis` | 0.95 |
| 取引頻度（警告のみ） | `TRADE_FREQUENCY_OUT_OF_PROFILE`（WARN） | 年あたり取引数がプロファイルの想定範囲外 | `trades`、期間 | `intraday`: 100〜600 回/年 |

- 1 つの候補に複数の理由コードがつく（例: 取引しないパスは `INSUFFICIENT_TRADES`・`NEGATIVE_EXPECTANCY`・`LOW_PSR`）。
- MinTRL は判定に使わず、「あと何日あれば PSR が閾値に届くか」として `why-not` に表示する（§9.6 の設計どおり）。
- 値が計算できないとき（null）は、その理由（例: `zero variance`）を記録し、**合格扱いにしない**。

### 3.2 DD の上限ゲートを P4 に入れない理由（M2、設計変更）

設計書 §3.3 は足切りの例に「DD 上限超過」を挙げている。しかし本基盤は**固定ロット（0.01）で研究し、ロットは P9 の資金配分で決める**（§12.1）。そのため、P4 時点の DD 金額や DD% はロットと初期資金の置き方しだいでいくらでも変わる。0.01 ロットの DD% で足切りをすると、閾値の意味がなくなる。

→ P4 では**ロットに依存しない基準**（破産・ロスカット・証拠金・PSR・期待値・取引数）だけで足切りする。DD のリスクは P9 で、モンテカルロにより「配分後のロットで DD が上限を超える確率」として評価する。DD の値は P4 でも記録し、`why-not` に表示する。

### 3.3 判定の再現性

- 判定は「ラウンド × プロファイルの内容ハッシュ × ゲートの実装バージョン」で一意に決まる。同じ組み合わせで再実行したら既存の結果を返す（冪等）。
- プロファイルの閾値を変えると、新しいフィルタ実行として**別に記録**する（古い判定は消さない）。閾値の変更回数は「研究者の自由度」なので、`filter show` に同じラウンドへの実行回数を表示する（§10.4 の考え方）。

---

## 4. データモデル（追記のみ、SCHEMA_VERSION 4）

設計書 §5.2 に合わせる。

| テーブル | 主な列 | 備考 |
|---|---|---|
| `filter_execution` | filter_id, round_id, profile_name, profile_hash, component_name, component_version, n_candidates, n_pass, created_at | 1 回の `filter run` |
| `evaluation` | evaluation_id, candidate_id, stage = `FILTER`, evaluator_name, evaluator_version, config_hash, input_run_ids_json, input_hash, values_json, created_at | ゲートごとの計算値（PSR、MinTRL など） |
| `decision` | decision_id, candidate_id, stage = `FILTER`, outcome（PASS/FAIL）, component_name, component_version, reason_codes_json, details_json, filter_id, created_at | 候補ごとの判定。全理由コードと、ゲートごとの値・閾値・合否を `details_json` に |
| `reason_code` | code, category, description, severity | カタログ（§5.4 の表から登録） |

候補の「現在の状態」は列として持たず、最新の判定から導く（§5.3 の設計どおり）。

---

## 5. ストレージの改善（M4、P3 の積み残し）

| 項目 | 現在（P3） | 変更後 |
|---|---|---|
| 生の日次 CSV（`frames_pdaily`） | 無圧縮で保存 | **gzip 圧縮**で保存（監査用の原本として残す） |
| 正規化 CSV（`daily_canonical_csv`） | ファイルを保存 | **ファイルは保存せず、ハッシュだけ記録**（再現性の比較は従来どおりハッシュで行う。E1 の約束は変えない） |
| Parquet（`daily_parquet`） | 保存 | 変更なし（**日次データの正本**） |
| 取込時のメモリ | 全行を辞書で保持 | パスごとに処理して、ピークメモリを抑える |

- これから実行するジョブだけに適用する。P3 で保存済みの成果物はそのまま残す（追記のみ）。
- 見込み: 1 ラウンドあたりの保存量は約 1/5〜1/10。

---

## 6. 変更・新規作成するファイル（予定）

| 種類 | ファイル |
|---|---|
| 設定 | `configs/strategy_profiles.yaml`（新規）、`configs/strategies/rl_smoketest.yaml`（`profile: intraday` を追加） |
| Python | `src/robustlab/stats/psr.py`、`src/robustlab/filtering/gates.py`、`src/robustlab/filtering/run.py`、`src/robustlab/core/models.py`（プロファイル）、`src/robustlab/storage/db.py`（v4）、`src/robustlab/cli/main.py`、`src/robustlab/optimization.py`（M4） |
| テスト | PSR・MinTRL（R の式との一致、性質テスト）、各ゲートの境界値、全ゲートが評価されること、冪等性、`why-not` の表示、DB 移行 v3→v4、M4 の保存形式 |
| 文書 | `docs/ARCHITECTURE.md`、`docs/DESIGN_CHANGELOG.md`、`docs/research/SOURCES.md`（S-PA-R を A に更新）、`docs/research/FINDINGS.md`、`CLAUDE.md` |

PSR のテストは、(a) R の `ProbSharpeRatio` / `MinTrackRecord` と同じ式を独立に書いた値との一致、(b) 性質（SR = SR* なら PSR = 0.5、標本長が増えると PSR は 1 に近づく、尖度が大きいほど PSR は下がる、正規分布なら γ₃ = 0・γ₄ = 3 で単純な式に一致）、(c) R の例の入力での値、の 3 種類で行う。原著の掲載値が確認できたら追加する。

---

## 7. 完了条件

1. `rlab filter run or_9795248161825c3e` で 1,044 パスすべてに判定がつき、全ゲートの結果が保存される
2. `rlab why-not <candidate_id>` で、ゲートごとの値・閾値・合否、全理由コード、MinTRL が表示される
3. `rlab filter show <filter_id>` で、理由コード別の件数（1 候補に複数可）と合格数が表示される
4. 同じラウンド・同じプロファイルでの再実行は冪等。閾値を変えると別の実行として記録される
5. PSR・MinTRL が R PerformanceAnalytics の式と一致する（テスト）
6. 判定に理由コード・コンポーネント名・バージョン・プロファイルのハッシュが記録される（CLAUDE.md の不変条件）
7. Linux で `uv run pytest` が全件通る

あなたの PC での作業は、`git pull` のあとに 1〜3 のコマンドを実行して表示を貼るだけになる見込み（MT5 は使わない）。

---

## 8. 承認をお願いしたい判断（要約）

| ID | 判断 | 推奨 | 理由 |
|---|---|---|---|
| M1 | PSR は原著の式（尖度を含む）を使う。R の既定値（尖度を無視）とは違うことを明記する | 原著の式 | 設計（§10.2）どおり。日次データは十分な長さがあり、裾が厚いほど PSR が下がる＝保守側 |
| M2 | DD の上限による足切りを P4 から外し、P9（配分後のロットでのモンテカルロ）で評価する | 外す | 固定ロットの DD% はロットしだいで意味が変わる。DD の値は記録・表示はする |
| M3 | P4 の対象は最適化ラウンドの全パスに限る（単一テストは P8 で扱う） | そのとおり | P4 の目的は「大量の試行の足切り」。単一テストは検証段階でまとめて扱う |
| M4 | 日次データの保存を「Parquet が正本、生 CSV は gzip、正規化 CSV はハッシュのみ」に変える | 変える | 保存量を約 1/5〜1/10 に。再現性の比較方法（E1）は変えない |
| M5 | 既定の閾値: PSR ≥ 0.95（SR* = 0）、取引数の下限 30、日次データ 60 日以上、証拠金維持率 200% 以上 | この値 | PSR 0.95 は片側 5% 有意に相当する慣例。30 と 60 は推定の安全装置（本体は PSR）。200% は一般的なマージンコール水準（100%）に余裕を持たせた値。いずれもプロファイルで変更でき、変更は記録される |
| M6 | 取引頻度がプロファイルの想定外でも、足切りせず警告（WARN）にとどめる | WARN | 頻度は戦略の性格の手がかりで、合否の根拠としては弱い |
