# HOW_TO_USE — EA ロバストネス研究基盤の使い方（初心者向け）

作成日: 2026-10-12。対象: リポジトリの現在のコード（P1〜P10 完了時点）。
この文書は**調査結果とマニュアル**です。作成にあたって、コード・閾値・データベース・Holdout・研究結果は何も変更していません。

---

## 0. 最初に読んでほしいこと

### 0.1 この基盤は何をするものか

トレンドフォロー・モメンタム・平均回帰などの売買ルール（EA）を、MT5 のストラテジーテスターで**公平に**試し、

- たまたま過去に合っただけのパラメータ（**過学習**＝過去のデータに合わせすぎて、将来は通用しない状態）を見分け、
- 少しパラメータを変えても成績が崩れない「**ロバスト（頑健）な領域**」を探し、
- 別の期間でも通用するかを確かめ、
- 最後に、資金を守れるロット（取引量）を計算する

ための研究の道具です。

### 0.2 いちばん大事な 3 つの区別

この基盤を使うときは、次の 3 つを**絶対に混同しない**でください。

| 区別 | 意味 | 今の状態 |
|---|---|---|
| ① 基盤が動く | コマンドが正しく動き、計算や記録が設計どおりに行われる | **はい**。P1〜P10 は実機（Windows・MT5）または Linux のテストで確認済み（§3） |
| ② 統計的な基準を通過した | ある EA が、事前に決めた合格基準（P4〜P8）を満たした | **まだ 1 件もない**。今入っている EA（`RL_SmokeTest`、移動平均の交差）は仕組みの確認用で、実データでは P4 の合格が 0 件 |
| ③ 実際に儲かる | 将来の本番取引で利益が出る | **誰にも保証できない**。②を通過しても「事前の基準で反証されなかった」だけで、利益の保証ではない |

たとえるなら、①は「体温計が正しく動く」、②は「ある人の体温が基準内だった」、③は「その人が一生健康である」です。体温計が完璧でも、③は言えません。

> 現在のマーチン・ナンピン EA は、この基盤にはまだ登録されていません。この基盤で検証済みの「有効な投資戦略」は、**現時点で 1 つもありません**。

### 0.3 この文書の読み方

- 用語は初めて出たところで短く説明します。まとめて確認したいときは **§12 用語集** を見てください。
- コマンドの横の印:
  - **［実機確認済み］** … Windows の実機で実行して動作を確認した
  - **［未確認］** … Linux の自動テストでは動いたが、実機ではまだ実行していない
- コマンドは Windows PowerShell 用です。1 つの枠に 1 行です。1 行ずつ貼り付けて Enter を押し、結果を確認してから次に進んでください。
- あなたの PC では `uv` に PATH が通っていないため、コマンドは `.\.venv\Scripts\rlab.exe` で実行します（リポジトリのフォルダ `C:\work\repo\rlab` に `cd` してから）。
- `or_9795…` のような ID は、**実行するたびに画面に出た値**に置き換えてください。ID はその場で表示されたものを控えて使います（§6）。

---

## 1. 全体の流れ（P1〜P10 を 1 本の流れとして）

スポーツのスカウトにたとえます。大勢の選手（＝パラメータの組合せ）から、本当に信頼できる選手を選び、最後に「どれだけ出場させるか（＝ロット）」を決める流れです。

```
[候補を作る]        [悪い候補を落とす]   [安定性を調べる]          [別の期間で確かめる]          [お金のリスク]       [ものさし自体の検査]
P1 単一テスト  →   P4 足切り       →   P5 ロバストネス       →   P8a 複数期間の検証     →   P9 リスクと     ／   P10 帰無仮説テスト
P2 指標の計算       （PSR など）        P6 統計評価（DSR）        P8b OOS（一度きり）          資金配分               （偶然の合格率）
P3 全組合せの最適化                     P7 選抜
```

| 段階 | ひとことで | スカウトでいうと |
|---|---|---|
| P1 | MT5 で 1 回バックテストして、結果を確実に記録する | 選手を 1 試合だけ見る |
| P2 | その結果から成績の指標（PF・Sharpe・最大 DD など）を自前で計算し、MT5 の値と照合する | 試合の記録を正確につける |
| P3 | パラメータの全組合せ（グリッド）を MT5 の最適化で一気に試す | 全選手の試合を見る |
| P4 | 取引数が少なすぎる、統計的に弱い（PSR が低い）などの候補を落とす | 明らかに基準外の選手を外す |
| P5 | 隣のパラメータでも成績が似ているか（島）を調べる。**評価値を出すだけで合否は決めない** | 調子の波が小さいか、たまたまの 1 試合ではないかを見る |
| P6 | たくさん試したことによる「まぐれ当たり」の補正（DSR）と、成績のばらつき（ブートストラップ）を計算する | 何百人も見れば、まぐれの好成績も出ることを差し引く |
| P7 | P4〜P6 の結果から、ゲート（孤立した山・近傍が不安定など）で落とし、上位 5 件まで選ぶ | 代表選手を最大 5 人選ぶ |
| P8a | 最適化に使っていない別の期間で、パラメータを固定したまま試す | 別のシーズンでも通用するか見る |
| P8b | 封印しておいた最後の期間（Holdout）で**一度だけ**試す | 本番前の最終試験。やり直し不可 |
| P9 | 最大 DD・破産・ストップアウトの確率が上限内に収まる最大ロットを計算する | 出場時間を、ケガの危険が許容範囲に収まるように決める |
| P10 | 「本当は優位性がない」価格で同じ流れを回し、それでも合格してしまう割合（偶然の合格率）を測る | スカウトの目そのものが甘くないかを検査する |

**重要:** P7 で選ばれたことは優位性の証拠ではありません。優位性がない合成データでも、P7 は 73% の回で何かを選びます（`docs/research/p7_validation/RESULTS.md`）。P7 は「P8 に進める候補を絞る段階」です。

---

## 2. ロバスト性と「島」— 具体例で理解する

### 2.1 パラメータのグリッドとは

たとえば移動平均の交差 EA に「短期の期間（Fast）」と「長期の期間（Slow）」という 2 つのパラメータがあるとします。Fast を 5〜40、Slow を 20〜160 で全部試すと、表（**グリッド**＝格子）のように成績が並びます。1 マスが 1 つの**候補**（パラメータの組合せ）です。

### 2.2 壊れやすい EA と頑健な EA

数字は日次 Sharpe（1 日あたりのリターンの平均 ÷ ばらつき。大きいほど安定して儲かった。§12 参照）。説明用の架空の値です。

**例 A: 壊れやすい EA（尖った山）**

| Fast＼Slow | 40 | 45 | 50 |
|---|---|---|---|
| 11 | −0.03 | −0.02 | −0.01 |
| 12 | 0.00 | **0.15** | 0.01 |
| 13 | −0.04 | 0.02 | −0.02 |

真ん中（Fast 12・Slow 45）だけが飛び抜けて良く、1 つずれると損をしています。過去のデータの細かいクセにたまたま合っただけの可能性が高く、将来は崩れやすいと考えます。

**例 B: 頑健な EA（台地）**

| Fast＼Slow | 40 | 45 | 50 |
|---|---|---|---|
| 11 | 0.06 | 0.07 | 0.07 |
| 12 | 0.08 | **0.08** | 0.06 |
| 13 | 0.07 | 0.05 | 0.07 |

真ん中の成績は例 A より低いですが、周りもほぼ同じです。相場が少し変わってパラメータの「最適な位置」がずれても、成績が大きく崩れにくいと期待できます。

この基盤は、**例 A のような尖った山を避け、例 B のような台地を探す**ように作られています。

### 2.3 基盤が使う指標（P5 で計算、P7 で判定）

| 指標 | 意味 | 例 A | 例 B |
|---|---|---|---|
| スコア | その候補の日次 Sharpe | 0.15 | 0.08 |
| 近傍 | 縦・横・斜めに 1 マス隣の候補（**チェビシェフ距離 1**＝縦横斜めどれでも 1 歩）。2 軸なら最大 8 個 | 8 個 | 8 個 |
| **Robust Score** | 自分と近傍の 9 個の値のうち、**下から 25% の位置の値**（25% 分位点）。「周りを含めた悪いほうの成績」 | 9 個を小さい順に並べた 3 番目 = **−0.02** | 3 番目 = **0.06** |
| 近傍の中央値 | 自分を除く近傍の真ん中の値 | −0.02 | 0.07 |
| Peak Ratio（山の比） | 自分 ÷ 近傍の中央値。大きいほど「孤立した山」 | 中央値 ≤ 0 なので計算しない | 0.08 ÷ 0.07 ≈ 1.14 |
| 感度 | 軸方向に 1 歩動いたときのスコアの変化の大きさ（絶対値） | 大きい | 小さい |

**P7 のゲート**（`configs/selection_profiles.yaml` の `default`、`docs/metrics_definitions.md`「判定と選抜」）:

- `ISOLATED_PEAK`（孤立した山）: 島に属さない、Peak Ratio > 1.5、または近傍の中央値 ≤ 0 → 例 A はここで落ちる
- `NEIGHBORHOOD_UNSTABLE`（近傍が不安定）: Robust Score ≤ 0、または近傍に破産・ストップアウトが 1 つでもある → 例 A はここでも落ちる
- `NEIGHBORHOOD_INSUFFICIENT`（近傍が足りない）: 数えられる近傍が ⌈(3^d − 1)/2⌉ 個未満（2 軸なら 4 個未満）。取引しなかった近傍（`NO_TRADES`）は数えない

例 B はこれらのゲートを通過します（ほかの条件も満たせば）。

### 2.4 島・メドイド・サブ期間

- **島（region）**: P4 に合格した候補のうち、縦・横・斜めにつながっているもののまとまり。2 マス以上で島と呼びます。**面積** = 島に含まれる候補の数。
- **メドイド（medoid）**: 島の「中心にいちばん近い代表」。島のほかの全候補へのチェビシェフ距離の合計がいちばん小さい候補です（平均値の位置は実在しないかもしれないので、実在する候補から選ぶ）。P7 は島ごとに 1 件だけ代表を選び、残りは `REDUNDANT_IN_REGION`（同じ島の重複）にします。
- **端（edge）**: グリッドの端にあり、近傍が本来の半分未満の候補。外側の様子が分からないので警告（`EDGE`）を出します。→ パラメータの範囲は、良さそうな領域が端に来ないよう広めにとります。
- **サブ期間の一貫性**: 暦年ごとの損益と Sharpe。**黒字年の割合**（positive_year_share）、最悪の年、年ごとの Sharpe のばらつき。特定の 1 年だけで稼いでいる候補には `SUBPERIOD_INCONSISTENT` の警告が付きます。

> P5 は**値を出すだけで合否を決めません**。判定は P7 だけが行います（C59）。

---

## 3. 実装状況（ソースとテストから判断）

| 段階 | 状態 | 根拠（主なファイル） | 実機での確認 |
|---|---|---|---|
| P1 単一テスト | 実装済み | `src/robustlab/single_backtest.py`、`mql5/Include/RobustLab/Telemetry.mqh` | 済み（`verification/p1/RECORD.md`） |
| P2 指標計算 | 実装済み | `src/robustlab/metrics/` | 済み |
| P3 最適化 | 実装済み | `src/robustlab/optimization.py`、`generation/grid.py` | 済み（`verification/p3/RECORD.md`） |
| P4 足切り | 実装済み | `src/robustlab/filtering/` | 済み（実データでは合格 0 件） |
| P5 ロバストネス | 実装済み | `src/robustlab/robustness/` | 済み |
| P6 統計評価 | 実装済み | `src/robustlab/stat_eval/` | 済み |
| P7 選抜 | 実装済み | `src/robustlab/selection/` | 済み（実データでは対象 0 件のため「選抜 0 件・理由つき」の確認まで。実際に選ばれる経路は偽の端末のテストで確認） |
| P8a 複数期間検証 | 実装済み | `src/robustlab/validation/` | 診断モードで確認 |
| P8b OOS | 実装済み | `src/robustlab/oos/` | 練習用ワークスペースで確認。**本番の Holdout は未使用・封印中** |
| P9 リスクと資金配分 | 実装済み | `src/robustlab/risk/` | 診断モードで確認（`risk show` は未確認） |
| P10 帰無仮説テスト | 実装済み | `src/robustlab/nulltest/` | 判定は Linux で実施（A1 0.30%・A2 1.40%・A3 2.80%）。MT5 との忠実度チェックは実機で一致 |
| レポート（HTML・候補の資料） | **未実装** | — | `show --json` で JSON を出せるだけ |
| `rlab trace`（判定の経緯をたどる） | **未実装** | 設計書の §8.3（出力イメージ）・CLI 一覧・P8 の完了条件に記載あり | — |
| パイプラインの一括実行（P11） | **未実装** | — | 各段階を手で順に実行する |
| PBO・StepM・SPA（P12）、ポートフォリオ（P13）、Walk-Forward | **未実装** | 設計書の将来項目 | — |
| GUI | **なし** | — | §8 |

---

## 4. 準備（環境構築）

前提: Windows、Python 3.13、MT5 のポータブル版を `C:\MT5_verify` に置き、**デモ口座**でログインしてパスワードを端末に保存済み。リポジトリを `C:\work\repo\rlab` に置いています（今までの実機確認と同じ構成）。

### 4.1 最新版の取得と依存関係

```powershell
cd C:\work\repo\rlab
```
```powershell
git pull
```
```powershell
py -3.13 -m uv sync
```
- 成功: エラーなく終わり、`.venv\Scripts\rlab.exe` ができている。
- 確認:
```powershell
.\.venv\Scripts\rlab.exe --help
```
- 成功の表示: コマンドの一覧（`backtest`、`optimize`、`filter` …）が出る。
- 失敗したら: `py -3.13` が見つからない場合は Python 3.13 を入れ直す。`uv` が見つからない場合は `py -3.13 -m pip install uv` のあと、もう一度 `py -3.13 -m uv sync`。

### 4.2 端末の設定ファイル

`configs\terminal.example.yaml` を `configs\terminal.yaml` にコピーし（Git には入りません）、`terminal_path` と `common_files_dir` を自分の PC に合わせます。**ログイン ID・パスワードは書かないでください**（書くと拒否されます）。

```powershell
Copy-Item configs\terminal.example.yaml configs\terminal.yaml
```

### 4.3 Telemetry と EA を MT5 にコピーしてコンパイル

（`docs/runbooks/P3_E2E.md` §2 と同じ手順。EA 名は自分の EA に置き換えてください）
```powershell
Copy-Item -Force .\mql5\Include\RobustLab\Telemetry.mqh C:\MT5_verify\MQL5\Include\RobustLab\
```
```powershell
Copy-Item -Force .\mql5\Experts\RobustLab\RL_SmokeTest.mq5 C:\MT5_verify\MQL5\Experts\RobustLab\
```
```powershell
Start-Process -Wait "C:\MT5_verify\metaeditor64.exe" -ArgumentList "/portable", "/compile:`"C:\MT5_verify\MQL5\Experts\RobustLab\RL_SmokeTest.mq5`"", "/log:`"$PWD\compile.log`""
```
```powershell
Get-Content .\compile.log -Encoding Unicode | Select-String "Result|error"
```
- 成功: `Result: 0 errors`（MetaEditor の終了コードは成功でも 1 なので見ない）。
- 失敗したら: `error` の行の内容を見て EA を直す。

### 4.4 ワークスペースについて

結果はすべて `workspace\` フォルダに保存されます（§6）。**`workspace\` と `workspace_*\` には口座番号と IP アドレスを含むログが入るので、Git にコミットしたり人に送ったりしないでください。**

---

## 5. 新しい研究の始め方（段階ごと）

各段階で「何をするか／なぜ必要か／入力と実行／結果の読み方／成功の表示／失敗したとき」を書きます。

### 5.0 EA と設定ファイルを用意する（研究の入口）

**何をするか:** 研究する EA を基盤に登録する 2〜3 個の YAML ファイルを作ります。

| ファイル | 役割 | 例 |
|---|---|---|
| 戦略ファイル `configs/strategies/<名前>.yaml` | EA の場所と入力パラメータの一覧 | `configs/strategies/rl_smoketest.yaml` |
| 研究（study）ファイル `configs/studies/<名前>.yaml` | 銘柄・時間足・期間・**パラメータのグリッド**・テスターの条件 | `configs/studies/rl_smoketest_grid.yaml` |
| 単一テストの依頼ファイル `configs/requests/<名前>.yaml` | 1 つのパラメータで 1 回試す条件 | `configs/requests/smoketest_eurusd_h1.yaml` |

戦略ファイル（実例）:
```yaml
strategy_name: RL_SmokeTest
expert: RobustLab\RL_SmokeTest          # MQL5\Experts からの相対パス。.ex5 は付けない
mq5_path: ../../mql5/Experts/RobustLab/RL_SmokeTest.mq5
telemetry_version: "3"
inputs:                                  # 型は int / double / bool / string / enum / datetime
  - {name: InpFastPeriod, type: int}
  - {name: InpSlowPeriod, type: int}
  - {name: InpLots, type: double}
```
- `RL_` で始まる入力名は基盤の予約なので使えません。

研究ファイル（実例、**パラメータのグリッドはここで決めます**）:
```yaml
study_id: smoketest_grid_v1
strategy_file: ../strategies/rl_smoketest.yaml
symbol: EURUSD
timeframe: H1
from_date: 2022-01-03          # 開始日（この日を含む、サーバー時刻）
to_date: 2024-01-01            # 終了日（この日を含まない）
param_space:                   # 軸ごとに start・step・stop（stop を含む）
  InpFastPeriod: {start: 5, step: 1, stop: 40}
  InpSlowPeriod: {start: 20, step: 5, stop: 160}
fixed:                         # 最適化しない値（ロットは固定）
  InpLots: 0.01
tester: {model: REAL_TICKS, execution_delay_ms: 0, deposit: 10000, currency: USD, leverage: 100}
max_passes_per_job: 600        # 1 回の最適化の上限。超えると自動で分割（既定 2000）
coverage_tolerance_days: 7
```
- 省略可能な `strategy_family`（戦略の家族名）は、P6 で「同じ家族で何回試したか」を数える単位です。省略すると戦略名になります。
- 期間は Holdout（2024-07-01〜2026-06-30）と前後 10 日（**エンバーゴ**＝境目の情報の漏れを防ぐすき間）に重ならないようにします。重なると終了コード 3 で拒否されます（§7）。
- `model` は `REAL_TICKS`（実ティック。正確だが遅い）か `OHLC_M1`（1 分足。速い）。

**なぜ必要か:** 設定をファイルに書いて残すことで、誰がいつ実行しても同じ条件になり（再現性）、ファイルの内容から ID が作られるので、「どの条件で出した結果か」を後から必ずたどれます。

ほかの設定（変えるときは慎重に）:
- `configs/data_partition.yaml` … Holdout の期間。ワークスペースで初めて使ったときに固定され、**後から変えると、そのワークスペースのすべての実行が失敗します**。
- `configs/strategy_profiles.yaml` … P4 の足切りの基準（戦略の型ごと: `intraday`・`swing_trend`・`mean_reversion`・`scalping`）。
- `configs/selection_profiles.yaml` … P7 の基準。`configs/risk_profiles.yaml` … P9 の基準。`configs/cost_stress.yaml` … P8 の追加コスト。

### 5.1 P1・P2: 単一テストと指標の計算

**何をするか:** 1 つのパラメータで 1 回バックテストし、約定・日次エクイティなどを記録して、指標を計算します。
**なぜ必要か:** 最適化の前に、EA と Telemetry（記録用の部品）が正しく動き、MT5 の値と自前の計算が一致することを確かめるためです。

```powershell
.\.venv\Scripts\rlab.exe backtest run configs\requests\smoketest_eurusd_h1.yaml
```
［実機確認済み］
- 成功の表示: `status: SUCCEEDED`、`run_id: rn_…`、`job_id: jb_…`。MT5 が自動で開いて閉じます（画面に触らない）。
- 同じ依頼をもう一度実行すると、既存の結果を返します（`--rerun` を付けると再実行して、前回と一致するか照合します）。
- 失敗したら: `status` が `FAILED`／`QUARANTINED` なら `error:` の行を読む。終了コード 3 は Holdout と重なっている（期間を直す）。
```powershell
.\.venv\Scripts\rlab.exe backtest show rn_xxxxxxxxxxxxxxxx
```
［実機確認済み］（`rn_…` は上で表示された値に置き換え）
```powershell
.\.venv\Scripts\rlab.exe metrics compute --all
```
［実機確認済み］
```powershell
.\.venv\Scripts\rlab.exe metrics show rn_xxxxxxxxxxxxxxxx
```
［実機確認済み］
- 結果の読み方: `-- comparison with MT5 --` の各行が一致（`MATCH` など）なら、自前の計算と MT5 の値が合っています。`-- metrics --` に PF・最大 DD・日次 Sharpe などが出ます。
- 参考: `rlab metrics runs <job_id>` もあります［未確認］。

### 5.2 P3: 全組合せの最適化

**何をするか:** 研究ファイルの `param_space` の全組合せを MT5 の最適化で試し、全パス（1 組合せ = 1 パス）の結果を集めます。
**なぜ必要か:** 一部だけ試すと、良い所だけを拾い読みすることになります。全部試して、山も谷も含めた「地形」を見るためです。

```powershell
.\.venv\Scripts\rlab.exe optimize run configs\studies\rl_smoketest_grid.yaml
```
［実機確認済み］
- 成功の表示: `status: SUCCEEDED`、ジョブごとに `passes=580/580` のように受け取ったパス数＝期待数、`round_id: or_…`（**控える**）。
- 赤字で `SAFETY:` が出たら: クラウドやリモートのエージェントが使われた可能性があるので、結果を使わず原因を確認する（成功の条件は「remote 0・cloud 0」）。
- 失敗したら: パス数が足りない場合は FAILED になります。EA が最適化中に `INIT_PARAMETERS_INCORRECT` を返していないか確認（無効な組合せは「取引しないパス」にする）。
```powershell
.\.venv\Scripts\rlab.exe optimize show or_xxxxxxxxxxxxxxxx
```
［実機確認済み］ 期待: `passes_stored` が全組合せ数、`chunks_succeeded=2/2` など。
```powershell
.\.venv\Scripts\rlab.exe trials show RL_SmokeTest
```
［実機確認済み］ 試行回数の合計（捨てた試行も含む）。P6 の補正に使われます。
- 1 つのパスを詳しく見る: `rlab optimize pass <round_id> <candidate_id>` ［実機確認済み］

### 5.3 P4: 足切り

**何をするか:** 戦略の型に合ったプロファイルで、取引数・日数・PSR・証拠金維持率などの基準を満たさない候補を落とします。
**なぜ必要か:** 取引が数回しかない候補や、統計的に偶然と区別できない候補を、後の段階に持ち込まないためです。
**注意:** 最大 DD の上限はここでは見ません（ロットに依存するため P9 で扱う、C51）。

```powershell
.\.venv\Scripts\rlab.exe filter run or_xxxxxxxxxxxxxxxx --profile swing_trend
```
［実機確認済み］
- 成功の表示: `filter_id: fl_…`（控える）、合格・不合格の件数、理由ごとの件数。
- 結果の読み方: 合格は「次の段階に進んでよい」という意味で、利益の予測ではありません（画面にも note が出ます）。
```powershell
.\.venv\Scripts\rlab.exe filter show fl_xxxxxxxxxxxxxxxx
```
［実機確認済み］
```powershell
.\.venv\Scripts\rlab.exe why-not cd_xxxxxxxxxxxxxxxx --filter fl_xxxxxxxxxxxxxxxx
```
［実機確認済み］ **ある候補がなぜ落ちたか**を表示します。ゲートごとに `value`（実際の値）と `threshold`（基準）が並び、PSR、あと何日分のデータがあれば足りるか（MinTRL）も出ます。P7 の判定があればそれも表示されます。
- 合格が 0 件だったら: それも正しい結果です。基準を緩めるのではなく、「この EA・この期間では証拠が足りない」と記録します。

### 5.4 P5: ロバストネス（島を探す）

**何をするか:** §2 の Robust Score・近傍・島・メドイド・感度・サブ期間を、全候補について計算します。
**なぜ必要か:** 尖った山（例 A）と台地（例 B）を区別するためです。

```powershell
.\.venv\Scripts\rlab.exe robust run or_xxxxxxxxxxxxxxxx --filter fl_xxxxxxxxxxxxxxxx
```
［実機確認済み］
- 成功の表示: `robust_id: rb_…`（控える）、島の数とそれぞれの面積・メドイド。
```powershell
.\.venv\Scripts\rlab.exe robust show rb_xxxxxxxxxxxxxxxx
```
［実機確認済み］
```powershell
.\.venv\Scripts\rlab.exe robust map rb_xxxxxxxxxxxxxxxx --metric robust
```
［実機確認済み］ グリッドを文字の地図で表示します（`--metric score` で元の Sharpe、`robust` で Robust Score）。
- 結果の読み方: `regions: 0  reason: NO_FILTER_PASS` は「P4 合格がないので島もない」、`ALL_ISOLATED` は「合格はあるが全部孤立」。どちらも正しい結果です。

### 5.5 P6: 統計評価（まぐれの補正とばらつき）

**何をするか:** DSR（たくさん試したことを補正した Sharpe の信頼度）と、ブートストラップによる Sharpe・期待値・最大 DD の区間を計算します。
**なぜ必要か:** 1,000 通り試せば、優位性がなくても 1 つくらいは好成績が出ます。その「試した回数」を差し引いて評価するためです。

```powershell
.\.venv\Scripts\rlab.exe stats run or_xxxxxxxxxxxxxxxx --filter fl_xxxxxxxxxxxxxxxx
```
［実機確認済み］（既定は再標本 2,000 回。`--seed` を省くと自動で決めたシードを記録）
- 成功の表示: `stat_id: st_…`（控える）。
- ブートストラップの対象: P4 合格・P5 のメドイド・`--candidate` で指定した候補。
```powershell
.\.venv\Scripts\rlab.exe stats show st_xxxxxxxxxxxxxxxx
```
［実機確認済み］
- 結果の読み方: DSR は**警告と順位づけにだけ使い、単独で合否を決めません**（C62）。値には `DISCOVERY_IN_SAMPLE`（最適化に使った期間の中の値）と付いており、別期間の証拠ではありません。

### 5.6 P7: 選抜

**何をするか:** §2.3 のゲートで落とし、島ごとに代表 1 件、Robust Score・DSR・最大 DD でパレートの層と平均順位を作り、上位 5 件まで `SELECTED` にします。
**なぜ必要か:** 後の P8 は期間を「使い切る」検証なので、持ち込む候補を少数に絞る必要があるからです。

```powershell
.\.venv\Scripts\rlab.exe select run or_xxxxxxxxxxxxxxxx --filter fl_xxxxxxxxxxxxxxxx --robust rb_xxxxxxxxxxxxxxxx --stats st_xxxxxxxxxxxxxxxx
```
［実機確認済み］
- 成功の表示: `sel_id: sl_…`（控える）、`SELECTED` の件数。
- 0 件の場合: `NO_ELIGIBLE` など理由付きで正常終了します。
```powershell
.\.venv\Scripts\rlab.exe select show sl_xxxxxxxxxxxxxxxx
```
［実機確認済み］ 候補ごとの判定は `--candidate cd_…` で。理由コード: `ISOLATED_PEAK`、`NEIGHBORHOOD_UNSTABLE`、`NEIGHBORHOOD_INSUFFICIENT`、`REDUNDANT_IN_REGION`、`DOMINATED`、`NOT_SELECTED_BUDGET` など。

### 5.7 P8a: 複数期間の検証

**何をするか:** 選ばれた候補のパラメータを**固定したまま**、最適化に使っていない期間（例: V0・V1）でバックテストし、損失・破産・追加コストへの弱さ・統計的な証拠（Holm 法で補正した p 値）を確認します。
**なぜ必要か:** 最適化した期間で良いのは当たり前です。見ていない期間でも通用するかが本当の試験です。

入力: 検証計画ファイル（実例 `configs/validation_plans/rl_smoketest_v1.yaml`）
```yaml
label: rl_smoketest_v1
strategy_file: ../strategies/rl_smoketest.yaml
periods:
  - {name: V0, from: 2020-01-01, to: 2021-12-24}
  - {name: V1, from: 2024-01-11, to: 2024-06-20}
```
- 期間は Discovery 期間（最適化に使った期間）と Holdout（＋エンバーゴ）に触れてはいけません。

```powershell
.\.venv\Scripts\rlab.exe validate lock sl_xxxxxxxxxxxxxxxx --plan configs\validation_plans\rl_smoketest_v1.yaml
```
［実機確認済み（診断モード）］ 実行前に計画を**ロック（固定）**します。表示: `plan_id: vp_…`。
```powershell
.\.venv\Scripts\rlab.exe validate run vp_xxxxxxxxxxxxxxxx
```
［実機確認済み（診断モード）］ まず Discovery 期間を再実行して P3 と一致するか確かめ（再現性）、そのあと各期間を試します。
```powershell
.\.venv\Scripts\rlab.exe validate show vp_xxxxxxxxxxxxxxxx
```
［実機確認済み］ **閲覧も記録されます**（結果を見た事実を残すため）。
- 結果の読み方: `PASS` は「事前の基準に反証されなかった」。`VALIDATION_INSUFFICIENT_EVIDENCE` は「証拠不足」で、**優位性がない証明ではありません**。ほかに `REPRODUCIBILITY_MISMATCH`、`VALIDATION_RUIN`、`VALIDATION_PERIOD_LOSS`、`COST_FRAGILE`。
- 選ばれた候補がないとき（今の EA）: `--diagnostic --candidate cd_…` で流れだけ確認できます。診断の結果は判定に使われません。ただし**期間を見たことは露出台帳に残ります**。
- 計画を変えたいとき: ロック済みの計画は変更できません。新しい計画を作ると `VALIDATION_REVISED` と記録されます。

### 5.8 P8b: OOS（Holdout で一度きり）

**何をするか:** 封印してある Holdout 期間で、P8a の合格者を一度だけ試します。実行の最初に Holdout を「燃やし」（使用済みにし）、二度と使えなくします。
**なぜ必要か:** P8a まででも、研究者は何度も結果を見て判断しています。誰も見ていない最後の期間で試すことで、その影響を取り除くためです。

> **本番の Holdout は封印中です。本物の EA で P8a の合格者が出るまで使いません。** 練習は必ず練習用ワークスペースで行ってください。

練習（実機確認済み）:
```powershell
.\.venv\Scripts\rlab.exe oos lock vp_xxxxxxxxxxxxxxxx --rehearsal --candidate cd_xxxxxxxxxxxxxxxx --workspace workspace_rehearsal --partition configs\rehearsal\data_partition.yaml
```
```powershell
.\.venv\Scripts\rlab.exe oos run om_xxxxxxxxxxxxxxxx --i-understand-this-is-final --workspace workspace_rehearsal --partition configs\rehearsal\data_partition.yaml
```
```powershell
.\.venv\Scripts\rlab.exe oos show om_xxxxxxxxxxxxxxxx --workspace workspace_rehearsal
```
- `--i-understand-this-is-final` がないと実行されません。2 回目の実行は拒否されます。
- 結果の読み方: ゲートは `OOS_BELOW_PREDICTION_BAND`（P8a から予想した範囲を下回った）、`OOS_LOSS`、`OOS_RUIN`、`OOS_COST_FRAGILE`。FAIL は「エッジが消えた証明」ではありません。

### 5.9 P9: リスクと資金配分

**何をするか:** 口座の条件（証拠金・最小ロットなど）のスナップショットを取り、最大 DD・破産・ストップアウトの確率が上限内に収まる最大ロットを、いくつものシナリオ（取引順の入れ替え、ボラティリティ 1.5 倍、コスト増など）で計算します。
**なぜ必要か:** 優れた戦略でも、ロットが大きすぎれば破産します。「資金を守る」ことが最優先だからです。

```powershell
.\.venv\Scripts\rlab.exe risk snapshot --symbol EURUSD --out workspace\snapshot_eurusd.json
```
［実機確認済み］ MT5 を起動・ログインした状態で実行（Windows のみ）。口座番号・名義・サーバー名は保存されません。
```powershell
.\.venv\Scripts\rlab.exe risk run vp_xxxxxxxxxxxxxxxx --snapshot workspace\snapshot_eurusd.json
```
［実機確認済み（診断モード）］ 表示: `risk_id: rk_…`。
```powershell
.\.venv\Scripts\rlab.exe risk show rk_xxxxxxxxxxxxxxxx
```
［未確認］
- 結果の読み方: ロットが出るのは **P8a で PASS（診断ではない計画）の候補だけ**です。それ以外は `DIAGNOSTIC`（1 ロットあたりの指標のみ）。ロットは常に切り捨てで、最小ロットに届かなければ `INFEASIBLE_MIN_LOT`。ほかに `MARGIN_BUDGET_EXCEEDED`、`NO_ALLOCATION_MU_LCB`（期待値の下限が 0 以下）、`NOT_ELIGIBLE_OOS_FAILED`、`CURRENCY_MISMATCH`。
- P9 で安全に見えても、**優位性の証拠ではありません**。恒久的な相場の変化への耐性も未検証です。

### 5.10 P10: 帰無仮説テスト（ものさしの検査）

**何をするか:** 「EA の仕組みが予測に使える構造が何もない価格」（**帰無仮説 H0**）を作り、P4〜P8a と同じ判定の流れを 1,000 回まわして、それでも合格してしまう割合（**偶然の合格率**）を測ります。
**なぜ必要か:** 基準が甘いと、優位性がない EA でも合格してしまいます。その割合が 5% 以下であることを確かめ、基準そのものを検査するためです。

```powershell
.\.venv\Scripts\rlab.exe null run --archetype A1 --mode reproduction
```
［未確認（Linux で実行）］ モードは `judgement`（判定、型ごとに 1 回だけ・2 回目は `ALREADY_JUDGED` で拒否）、`reproduction`（事前シミュレーションの再現）、`power`（検出力の参考）。
```powershell
.\.venv\Scripts\rlab.exe null show nl_xxxxxxxxxxxxxxxx
```
［未確認］
- 型（archetype）は Python で作った模型です: A1 トレンド（移動平均）、A2 ナンピン、A3 マーチン。判定の結果: A1 0.30%・A2 1.40%・A3 2.80%（すべて上側限界 ≤ 5%）。
- 結果の読み方: `MET` は「この型の構造では、偶然の合格率が基準以下」。`INVALID_S1` は健全性チェック（H0 で損益の期待値が 0 になっているか）に失敗した無効な実行。S1 の通過は H0 の証明ではありません。
- MT5 との忠実度チェック（実機確認済み）: `rlab null fidelity-export` → MT5 でスクリプト `RL_ImportH0` → `rlab backtest run configs/requests/p10_fidelity_h4.yaml --workspace workspace_p10fid` → `rlab null fidelity-compare --workspace workspace_p10fid`。
- **注意:** 新しい種類の戦略（平均回帰など）を P10 にかけるには、その構造を表す型を Python で追加する必要があります（§9）。

---

## 6. 結果の保存場所と ID

### 6.1 保存場所

| 場所 | 中身 |
|---|---|
| `workspace\robustlab.sqlite` | すべての記録（候補・実行・評価・判定・試行台帳・露出台帳など）。研究結果のテーブルは**追記のみ**（書き換え・削除しない） |
| `workspace\artifacts\` | 約定・日次エクイティ（Parquet）・レポートなどのファイル。内容の SHA-256 で名前が付き、書き換えられない |
| `workspace_rehearsal\`、`workspace_p10fid\` | 練習用・忠実度チェック用の別ワークスペース（`--workspace` で指定） |

どのコマンドも `--workspace` でワークスペースを選べます（既定は `workspace`）。

### 6.2 ID の見方

ID は「接頭辞＋内容の SHA-256 の先頭 16 桁」で、**同じ内容からは必ず同じ ID** になります（`src/robustlab/core/ids.py`）。そのため、同じ条件でもう一度実行すると `ALREADY_DONE`（実行済み）と表示され、二重に記録されません。

| 接頭辞 | 意味 | どこで表示されるか |
|---|---|---|
| `sv_` | EA のバージョン（.ex5・.mq5 のハッシュ） | `backtest show` など |
| `cd_` | 候補（EA＋銘柄＋時間足＋パラメータ） | `optimize show`、`filter show`、`select show` |
| `rn_` | 単一テストの実行（候補＋期間＋条件） | `backtest run` |
| `jb_` | ジョブ（1 回の試み。時刻つき） | `backtest run`、`jobs list` |
| `or_` | 最適化ラウンド | `optimize run` |
| `fl_` | 足切りの実行（P4） | `filter run` |
| `rb_`／`rg_` | ロバストネスの実行／島（P5） | `robust run`／`robust show` |
| `st_` | 統計評価の実行（P6） | `stats run` |
| `sl_` | 選抜の実行（P7） | `select run` |
| `vp_` | 検証計画（P8a） | `validate lock` |
| `om_` | OOS マニフェスト（P8b） | `oos lock` |
| `rk_` | リスク評価（P9） | `risk run` |
| `nl_` | 帰無仮説テスト（P10） | `null run` |
| `ev_`／`dc_` | 評価／判定の 1 行 | 内部（`show` の中に出る） |

ID を忘れたとき: `rlab jobs list`（最近のジョブ）、`rlab trials show <戦略名>`（ラウンドの一覧）。ほかの ID は、その前の段階の `show` に表示されます。

### 6.3 詳細・不合格の理由・最終レポート

- 詳細: 各段階の `show`。`--json` を付けると機械で読める JSON になります。
- 不合格の理由: `rlab why-not cd_… [--filter fl_…]`（P4 と P7 の理由）、`rlab select show sl_… --candidate cd_…`、`rlab validate show vp_… --candidate cd_…`、`rlab risk show rk_… --candidate cd_…`。
- **最終レポート: 未実装です。** HTML のレポートや候補ごとの資料（設計書の「簡易レポート」）はまだありません。今は各段階の `show --json` を保存して、手でまとめる必要があります。
- ログ本文は CLI では表示しません（口座番号・IP を含むため）。

---

## 7. 研究上の制約（なぜ守るのか）

| 制約 | 内容 | 理由 |
|---|---|---|
| Holdout の封印 | 2024-07-01〜2026-06-30（前後 10 日のエンバーゴ含む）は、P8b 以外では使えない。重なる期間を指定すると終了コード 3（Holdout Guard）で拒否 | 最後の試験の問題を、事前に見てしまわないため |
| Holdout は一度きり | P8b の実行の最初に Holdout を燃やす。再実行は拒否 | 何度も試せば、いつか偶然合格するため |
| パラメータの固定 | P8a・P8b・P9 では再最適化しない | 期間ごとに合わせ直すと、それ自体が過学習になるため |
| 事前登録 | 検証計画は実行前にロック。合格基準は結果を見た後に緩めない。未達は未達として記録 | 結果に合わせて基準を動かすと、何でも合格にできてしまうため |
| 露出台帳 | 結果を見た期間は記録され、それより後に作ったラウンドの検証には使えない | 一度見た期間は「見ていない期間」ではなくなるため |
| 試行の記録 | 捨てた試行も含めて試行台帳に記録 | P6 の補正（DSR）に必要。隠すと補正が甘くなる |
| 再実行 | 同じ内容は同じ ID。`--rerun` で再実行すると、前回との一致を照合する | 再現性を確かめるため |
| シード | 乱数を使う処理はシードを記録（P6 の `--seed`、P10 の固定シード 20261013 など） | 同じ乱数で同じ結果を再現するため |
| データの分離 | Discovery（最適化）・検証期間・Holdout を分ける | 学習と試験に同じデータを使わないため |
| MT5 の更新 | 研究のひと区切りの合間に手動で。比較の途中では更新しない | 途中で変わると、再現性の比較ができなくなるため |

---

## 8. GUI について

**GUI（画面で操作する道具）はありません。** すべて CLI（コマンド）で操作します。MT5 の画面を使うのは、スクリプト（P10 の `RL_ImportH0` など）を実行するときと、EA をコンパイル・確認するときだけです。

CLI だけでできること:
- 研究の全段階（P1〜P10）の実行・表示・不合格理由の確認
- グリッドの文字の地図（`robust map`）
- JSON での出力（`--json`）。表計算ソフトや Python で読み込んでグラフにできる

CLI だけではできないこと:
- グラフ（エクイティ曲線・ヒートマップ）の表示
- 1 つのコマンドで全段階をまとめて実行（P11 は未実装）
- レポートの自動作成

---

## 9. 新しい古典戦略（トレンド・モメンタム・平均回帰など）を追加するには

### 9.1 EA ごとに作るもの（EA 固有）

1. **MQL5 の EA**（`mql5/Experts/RobustLab/<名前>.mq5`）。`RL_SmokeTest.mq5` を手本にして、次を必ず守ります。
   - `#include <RobustLab/Telemetry.mqh>`
   - `OnInit`: 先頭で `if(RL_IsFrameMode()) return INIT_SUCCEEDED;`、続けて `RL_TelemetryOnInit();`
   - `OnTick`: 最初の行で `RL_TelemetryOnTick();`
   - `OnTester`: `RL_TelemetryOnTester();`
   - `OnDeinit`: `RL_TelemetryOnDeinit(reason);`
   - `OnTesterInit`／`OnTesterPass`／`OnTesterDeinit`: 対応する `RL_` の関数を呼ぶ
   - 入力 `RL_JobId` などの `RL_` で始まる名前は予約（使わない）
   - **ロットは固定**（入力 `InpLots` など）。資金に応じたロット計算は EA ではなく P9 で行う
   - 最適化で `INIT_PARAMETERS_INCORRECT` を返さない（無効な組合せは「取引しないパス」にする）
   - テスター以外では取引しない作りにしておくと安全
2. **戦略ファイル**（`configs/strategies/<名前>.yaml`）: EA の場所と入力の一覧。
3. **研究ファイル**（`configs/studies/<名前>.yaml`）: 銘柄・時間足・期間・パラメータのグリッド。
4. **戦略の型に合う P4 プロファイルの選択**: トレンド → `swing_trend`、平均回帰 → `mean_reversion`、短期 → `intraday`／`scalping`。合うものがなければ `configs/strategy_profiles.yaml` に**研究を始める前に**新しいプロファイルを追加します（結果を見た後に作ってはいけません）。

### 9.2 そのまま使えるもの（汎用）

- P1〜P3: 単一テスト・指標計算・全組合せの最適化（どの EA でも同じ）
- P4〜P7: 足切り・ロバストネス・統計評価・選抜（設定ファイルで動く）
- P8a・P8b: 複数期間の検証・OOS
- P9: リスクと資金配分（1 ロットあたりの損益から計算するので、EA の種類に依存しない）

### 9.3 追加の実装が必要なもの

- **P10 の型（archetype）**: 今ある型は A1 トレンド（移動平均）・A2 ナンピン・A3 マーチンの 3 つで、Python の模型（`src/robustlab/nulltest/sim.py`、`configs/null_archetypes.yaml`）です。平均回帰やブレイクアウトなど構造が違う戦略には、新しい型を Python で書き、判定（1,000 ラウンド・固定シード・1 回だけ）を行う必要があります。既存の型の結果を別の構造の戦略に流用しないでください。
- **グリッド・マーチン用の P4 プロファイル**: 今はありません（必要なら事前に追加）。

---

## 10. 最初の研究を始める最短手順

例として、手元の `RL_SmokeTest` の代わりに「新しいトレンドフォロー EA」を研究する場合です。

1. EA を書く（§9.1 の約束を守る）→ MT5 にコピーしてコンパイル（§4.3）
2. 戦略ファイルと研究ファイルを作る（§5.0）。期間は Holdout とエンバーゴに重ねない。グリッドは良い領域が端に来ないよう広めに
3. 依頼ファイルで単一テスト → `metrics show` で MT5 との一致を確認（§5.1）
4. `optimize run` → `round_id`（`or_…`）を控える（§5.2）
5. `filter run … --profile swing_trend` → `filter_id`（§5.3）
6. `robust run` → `robust_id`、`robust map` で地形を見る（§5.4）
7. `stats run` → `stat_id`（§5.5）
8. `select run` → `sel_id`（§5.6）
9. 選ばれた候補があれば、検証計画を書いて `validate lock` → `validate run` → `validate show`（§5.7）
10. P8a で PASS が出たら、ここで初めて P8b（本番の Holdout）と P9 を検討する。それまでは Holdout を使わず、ロットも出さない

各段階で 0 件になったら、それが結果です。基準を緩めず、記録して次の仮説（別の EA・別の銘柄・別の時間足）に進みます。

---

## 11. 設計書と実装の食い違い（隠さず記録）

| 項目 | 設計書 | 実装 |
|---|---|---|
| `rlab trace` | `docs/ARCHITECTURE.md` の CLI 一覧と P8 の完了条件に記載 | **未実装**（コマンドがない） |
| 簡易レポート（HTML・候補の資料） | MVP に「簡易」として記載 | **未実装**。`show --json` のみ |
| P11 パイプライン一括実行 | 将来項目 | 未実装（各段階を手で実行） |
| P12 PBO・StepM・SPA、P13 ポートフォリオ、Walk-Forward | 将来項目 | 未実装 |
| P9 の配分方法 | fixed_fraction・dd_constrained・fractional_kelly・vol_target を別々の方法として記載 | 3 つの上限（安全ロット・ハーフケリー・1 取引あたり 1%・証拠金の予算）の**最小値をとる 1 つの規則**にまとめて実装。vol_target は未実装（C77〜C80） |
| P10 の帰無仮説 | 「ランダムなエントリー／価格のシャッフル」と記載 | 構造を持った型（A1〜A3）で再実行する方式に変更（C82、損益のシャッフルは使わない） |
| P10 の対象 | EA ごとに行う想定 | 型は Python の模型。新しい構造の戦略には型の追加実装が必要 |
| P4 のプロファイル | 戦略の型ごと | グリッド・マーチン向けのプロファイルはない |

---

## 12. 用語集

各用語: **意味** ／ なぜ使うか ／ 数字を見るときの注意。

### 成績の指標

- **純損益（net_profit）**: 期間中の利益の合計 − 損失の合計（スプレッド・手数料・スワップ込み）。／いちばん直接的な成績。／ロットや期間の長さで大きさが変わるので、候補どうしの比較には向かない。
- **PF（プロフィットファクター）**: 総利益 ÷ 総損失。1 より大きければ利益が出ている。／損益のバランスを 1 つの数で見られる。／取引数が少ないと簡単に大きくなる。マーチン・ナンピンは、破綻するまで PF が高く見えやすい。
- **勝率**: 勝ちトレードの割合。／EA の性格をつかむため。／勝率が高くても、1 回の負けが大きければ損をする（ナンピンが典型）。
- **期待利得（expected payoff）**: 1 取引あたりの平均損益。／取引のコストに耐えられるかの目安。／小さすぎると、わずかなコスト増で赤字になる。
- **Sharpe（シャープレシオ）**: リターンの平均 ÷ リターンのばらつき（標準偏差）。この基盤では**日次・年率化なし・無リスク金利 0**（`daily_sharpe`）。／「どれだけ安定して増えたか」を、ロットに依存せずに比べられる。基盤の中心のスコア。／日次の値なので小さく見える（年率の目安は × √252 ≈ × 16）。リターンが正規分布でない（大負けがまれに起きる）戦略では、実際より良く見える。
- **最大 DD（ドローダウン）**: 資産の最高値から、その後の最安値までの下落幅の最大。`balance_max_dd`（確定損益の残高ベース）と `equity_max_dd`（含み損を含むティック単位）がある。／「最悪どれだけ減るか」を知るため。資金を守る P9 の中心。／過去の最大 DD は将来の最大 DD の下限にすぎない。含み損を抱える EA では、残高ベースの DD は実態より小さく見える。
- **リカバリーファクター**: 純損益 ÷ 最大 DD。／下落に対する回復力。／DD が小さい期間だけを見ると大きく出る。
- **証拠金維持率**: 有効証拠金 ÷ 必要証拠金。下がりすぎるとストップアウト。／ポジションの持ちすぎを見るため。／P4 の値は「ラウンドの標準条件（固定ロット・初期資金）での値」で、安全性の判断ではない（C52）。
- **ストップアウト（ロスカット）**: 証拠金維持率が業者の基準を下回り、ポジションが強制的に決済されること。／破綻の一歩手前の事象として数える。
- **破産（bankrupt）**: 資産がほぼ 0 になること。／絶対に避けるべき事象。P9 では確率の上限を設ける。

### 統計の用語

- **過学習（オーバーフィッティング）**: 過去のデータの偶然のクセに合わせすぎ、将来は通用しない状態。／この基盤が防ぎたい最大の敵。
- **PSR（確率的シャープレシオ）**: 観測した Sharpe と日数・歪み（歪度・尖度）から、「本当の Sharpe が 0 より大きい」とどれだけ言えるかを 0〜1 で表す値。／取引日数が少ない・大負けがまれに起きる戦略を割り引いて評価できる。P4 の基準は 0.95。／**将来の利益の確率ではない**。たくさん試したことの補正は含まない（それは DSR）。
- **MinTRL（最小トラックレコード長）**: PSR が基準に届くのに必要な日数。／あと何日分のデータがあれば判断できるかの目安（`why-not` に表示）。
- **DSR（デフレーテッド・シャープレシオ）**: 試した回数（N）と成績のばらつき（V）から「まぐれでも出る最高の Sharpe」を計算し、それを超えているかを PSR と同じ形で表したもの。／多重検定（たくさん試すこと）の補正。／この基盤では警告と順位づけにだけ使い、単独で合否を決めない。値は最適化した期間の中のもので、別期間の証拠ではない。
- **多重検定**: たくさんの候補を試すと、優位性がなくても偶然どれかが良く見える問題。／P6 の DSR、P8a の Holm 法が対策。
- **p 値**: 「優位性がない」と仮定したとき、今の結果かそれ以上に良い結果が偶然出る確率。小さいほど偶然では説明しにくい。／P8a の統計ゲートに使う。／「優位性がある確率」ではない。
- **Holm 法**: 複数の候補の p 値を、候補の数に応じて厳しくして判定する方法。／同時にたくさん検定しても、誤って合格させる確率を抑えるため。
- **信頼区間・上側信頼限界**: 推定値の不確かさの幅。「95% 上側限界」は、本当の値がこれ以下だと 95% の確からしさで言える上の端。／P9（破産確率など）と P10（偶然の合格率）は、推定値ではなく上側限界で判定する（安全側）。
- **ブートストラップ**: 手元のデータから、ブロックごとに選び直して「ありえた別の結果」をたくさん作る方法（この基盤では定常ブートストラップ、`arch` ライブラリ）。／Sharpe や最大 DD のばらつきを知るため。／元のデータにない極端な事態は作れない。
- **モンテカルロ・シミュレーション**: 乱数でたくさんの架空のシナリオを作って結果の分布を見る方法。／P9 の破産確率、P10 の偶然の合格率などに使う。／作り方（仮定）が現実と違えば、結果も現実と違う。シードを記録する。
- **自己相関**: 今日のリターンが昨日のリターンと似る傾向。／あると、見かけの日数より情報が少なくなり、統計が甘くなる（`SERIAL_DEPENDENCE_HIGH` の警告）。
- **分位点（パーセンタイル）**: 小さい順に並べたとき、下から何 % の位置の値か。25% 分位点 = 下から 4 分の 1 の値。／Robust Score に使う。
- **パレート（の層）**: どの物差しでも他に負けていない候補の集まり。／複数の物差し（Robust Score・DSR・DD）で公平に順位をつけるため。
- **帰無仮説（H0）**: 「優位性がない」という仮説。この基盤の P10 では、EA の仕組みで予測できる構造が何もない価格。／判定の仕組みが偶然をどれだけ合格させるかを測るため。
- **偶然の合格率**: H0（優位性なし）の価格で回したとき、それでも P8a まで合格してしまう割合。基準は上側限界 ≤ 5%。／ものさしが甘くないかの検査。／実際の EA の誤判定率そのものではない（型の模型での値）。

### 期間・データの用語

- **IS（インサンプル）／Discovery 期間**: 最適化に使った期間。ここで良いのは当たり前。
- **OOS（アウトオブサンプル）**: 最適化に使っていない期間。ここでの成績が本当の試験。この基盤では P8a の検証期間と P8b の Holdout。
- **Holdout**: 最後の試験のために封印しておく期間（2024-07-01〜2026-06-30）。P8b で一度だけ使う。
- **エンバーゴ**: 期間の境目に設けるすき間（10 日）。境目をまたぐ情報の漏れを防ぐ。
- **露出（露出台帳）**: どの期間の結果を見たかの記録。一度見た期間は、後から作った研究の「見ていない期間」としては使えない。

### 資金配分の用語

- **ケリー基準・ハーフケリー**: 長期の資産の伸びを最大にする理論上の賭け率（期待値 ÷ 分散）。ハーフケリーはその半分。／理論値は変動が激しすぎるので、半分にして安全側にする。／期待値の推定が少しずれるだけで大きく変わる。この基盤では期待値の**下限**（μ_LCB）で計算し、さらに安全ロット・1 取引あたり 1%・証拠金の予算と比べて小さいほうを使う。
- **μ_LCB**: 1 取引あたりの期待損益の下側信頼限界（悪めに見積もった期待値）。0 以下ならロットを出さない（`NO_ALLOCATION_MU_LCB`）。
- **安全ロット**: 最大 DD・破産・ストップアウトの 3 つの確率（の上側限界）が、すべてのシナリオで上限内に収まる最大のロット。

### 基盤の用語

- **候補（candidate）**: EA＋銘柄＋時間足＋パラメータの 1 つの組合せ。
- **グリッド／パラメータ空間**: 試すパラメータの値の格子（研究ファイルの `param_space`）。
- **島（region）・メドイド・近傍・Robust Score**: §2 を参照。
- **Telemetry**: EA に組み込む記録用の部品（`Telemetry.mqh`）。約定・日次エクイティ・環境情報を書き出す。
- **Frames**: MT5 の最適化で、各パスの結果を集める仕組み。
- **冪等（べきとう）**: 同じ操作を何度しても結果が同じ。この基盤では同じ内容は同じ ID になり、二重に記録されない。
- **追記のみ**: 研究結果を書き換えず、新しい行を足すだけ。過去の判定を後から変えられないようにするため。
- **診断モード（DIAGNOSTIC）**: 仕組みの確認用に流れだけ走らせるモード。判定は書かず、結果を合否に使わない。

---

## 13. まとめ

### 13.1 現時点でできること

- 任意の MT5 EA（Telemetry を組み込んだもの）について、単一テスト・指標計算・全組合せの最適化を、再現性の確認つきで実行できる
- P4 足切り → P5 ロバストネス（島・Robust Score・感度・サブ期間）→ P6 DSR・ブートストラップ → P7 選抜を、CLI で順に実行し、不合格の理由を候補ごとに確認できる
- P8a の複数期間検証（計画のロック・露出の記録・Holm 法）、P8b の一度きりの OOS（練習用ワークスペースで練習可能）
- P9 で、資金を守る安全ロットを保守的に計算できる（P8a の合格者だけ）
- P10 で、トレンド（移動平均）・ナンピン・マーチンの 3 つの型について、判定の仕組みの偶然の合格率が 5% 以下であることを確認済み
- **ただし、検証を通過した有効な戦略はまだ 1 つもありません**（今の EA は仕組みの確認用で、実データの P4 合格は 0 件）

### 13.2 追加実装が必要なこと

- 最終レポート（HTML・候補ごとの資料）と `rlab trace`（設計書に記載あり、未実装）
- 全段階の一括実行（P11）
- 平均回帰・ブレイクアウトなど、新しい構造の戦略向けの P10 の型（Python）
- 必要に応じて、グリッド・マーチン向けの P4 プロファイル（研究開始前に追加）
- グラフ表示（GUI はないので、JSON を別の道具で可視化する）
- PBO・StepM・SPA（P12）、ポートフォリオ（P13）、Walk-Forward（将来項目）

### 13.3 利用者が次に行うこと（順番どおり）

1. **最初に研究する戦略の型を 1 つ決める。** P10 の型がすでにあるトレンドフォロー（移動平均の交差に近いもの）から始めるのが、追加実装なしで P1〜P10 を一通り使えるので最も近道です。
2. その EA を MQL5 で書く（§9.1 の約束：Telemetry の呼び出し、固定ロット、`INIT_PARAMETERS_INCORRECT` を返さない）。
3. **結果を見る前に**、研究の計画を決めて書き残す: 銘柄・時間足・Discovery 期間（Holdout とエンバーゴに重ねない）・パラメータの範囲・使う P4 プロファイル（`swing_trend` など）・P8a の検証期間。
4. 戦略ファイル・研究ファイル・依頼ファイルを作る（§5.0）。
5. 単一テストで動作と MT5 との一致を確認する（§5.1）。
6. 最適化 → P4 → P5 → P6 → P7 を順に実行し、各段階の ID と結果を記録する（§5.2〜§5.6）。
7. 0 件で止まったら、それを結果として記録し、基準は緩めずに次の仮説へ。選ばれた候補が出たら、P8a の計画をロックして検証する（§5.7）。
8. P8a の PASS が出て初めて、本番の Holdout（P8b）と P9 のロット計算を検討する。それまでは Holdout を封印し、ロットも出さない。

焦らなくて大丈夫です。0 件で止まることは失敗ではなく、「この基盤が過学習した候補を正しく落とした」という、基盤が仕事をした証拠でもあります。一つずつ進めましょう。
