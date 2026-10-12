# トレンドフォロー研究 v1 の実機手順（Windows）

条件: `docs/research/trend_v1/PREREGISTRATION.md`。結果の記録: `docs/research/trend_v1/RESULTS.md`。
**1 つの枠に 1 行**です。1 行ずつ貼り付けて Enter を押し、結果を確認してから次へ進んでください。
最適化の間は MT5 の画面に触らないでください。

## 第 1 回: 準備 → P1 単一テスト → P3 最適化

### 1. 最新版の取得

```powershell
cd C:\work\repo\rlab
```
```powershell
git pull
```
```powershell
py -3.13 -m uv sync
```

### 2. 既存の研究データのバックアップ（念のため。追記だけなので上書きはされません）

```powershell
Copy-Item -Recurse workspace workspace_backup_20261012
```
- `workspace_backup_20261012` も口座番号と IP を含みます。Git には入りません（`workspace_*` は除外済み）。人に送らないでください。

### 3. MT5 が動いていないことの確認

```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "C:\MT5_verify\terminal64.exe" }
```
何も表示されなければ次へ。EA は**コンパイルし直さないでください**（今の `.ex5` のまま使うと、過去の研究と同じ EA のバージョンとして記録されます）。

### 4. P1 単一テスト（グリッドの中心: 短期 15・長期 80、Discovery 期間の中）

```powershell
.\.venv\Scripts\rlab.exe backtest run configs\requests\trend_ma_h4_v1_single.yaml
```
- 期待: `status: SUCCEEDED`、`run_id: rn_…`。初回は 2014〜2015 年の 1 分足の履歴をダウンロードするので時間がかかります。
- `DATA_COVERAGE`、`QUARANTINED`、`FAILED` が出たら、**そこで止めて**表示を送ってください（期間はずらしません）。

```powershell
.\.venv\Scripts\rlab.exe metrics compute --all
```
- 期待: 新しいジョブの行が `computed (…)`、過去のジョブは `already computed (…)`。行の末尾に `MISMATCH:` が出たら止めて送ってください。

### 5. P3 最適化（25 パス）

```powershell
.\.venv\Scripts\rlab.exe optimize run configs\studies\trend_ma_h4_v1.yaml
```
- 期待: `status: SUCCEEDED`、ジョブ 1 つで `passes=25/25`、`round_id: or_…`。
- 赤字の `SAFETY:` が出たら、**そこで止めて**送ってください。
- OHLC_M1 での最適化は実機で初めてです（単一テストは確認済み）。

```powershell
.\.venv\Scripts\rlab.exe trials show RL_SmokeTest
```
- 期待: 合計（`total trials (raw N)`）が 1,069（過去の 1,044 ＋ 今回の 25）。違う数字なら、そのまま送ってください。

### 6. 送ってほしいもの

4 と 5 の各コマンドの表示をそのまま貼ってください（ログ本文は表示されないので、口座番号や IP は含まれません）。
特に `run_id`（`rn_…`）と `round_id`（`or_…`）が必要です。次の回（P4〜P7）のコマンドは、その ID を入れた形でお渡しします。

## 第 2 回: P3 の確認 → P4 足切り → P5 ロバストネス → P6 統計評価 → P7 選抜

**同じ PowerShell の窓で、上から順に**実行してください（途中で窓を閉じると、`$fl` などの変数が消えます。消えたら、その段階の `$… =` の行からやり直せば同じ ID が戻ります。同じ内容の実行は `ALREADY_DONE` になり、二重に記録されません）。

各段階は「① 普通に実行して表示を見る → ② 同じコマンドに `--json` を付けて ID を変数に入れる（実行済みなので何も増えない）→ ③ 変数を表示」の順です。

### 0. 場所の確認

```powershell
cd C:\work\repo\rlab
```

### 1. P3 の確認と P1 の指標

```powershell
.\.venv\Scripts\rlab.exe optimize show or_667b73e2bf2d8a70
```
- 期待: `passes_stored=25`、`chunks_succeeded=1/1`、ジョブの `agents=` が `remote 0`・`cloud 0`。
```powershell
.\.venv\Scripts\rlab.exe metrics show rn_62192f6024a089e2
```

### 2. P4 足切り（swing_trend）

```powershell
.\.venv\Scripts\rlab.exe filter run or_667b73e2bf2d8a70 --profile swing_trend
```
```powershell
$fl = (.\.venv\Scripts\rlab.exe filter run or_667b73e2bf2d8a70 --profile swing_trend --json | ConvertFrom-Json).filter_id
```
```powershell
$fl
```
- `fl_` で始まる ID が出れば OK。合格（passed）が 0 件でも、そのまま次へ進んでください（事前登録どおり P7 の「選抜 0 件」まで確認します）。

### 3. P5 ロバストネス

```powershell
.\.venv\Scripts\rlab.exe robust run or_667b73e2bf2d8a70 --filter $fl
```
```powershell
$rb = (.\.venv\Scripts\rlab.exe robust run or_667b73e2bf2d8a70 --filter $fl --json | ConvertFrom-Json).robust_id
```
```powershell
$rb
```
```powershell
.\.venv\Scripts\rlab.exe robust map $rb --metric score
```
```powershell
.\.venv\Scripts\rlab.exe robust map $rb --metric robust
```

### 4. P6 統計評価（シードは事前登録の 20261015）

```powershell
.\.venv\Scripts\rlab.exe stats run or_667b73e2bf2d8a70 --filter $fl --seed 20261015
```
```powershell
$st = (.\.venv\Scripts\rlab.exe stats run or_667b73e2bf2d8a70 --filter $fl --seed 20261015 --json | ConvertFrom-Json).stat_id
```
```powershell
$st
```

### 5. P7 選抜

```powershell
.\.venv\Scripts\rlab.exe select run or_667b73e2bf2d8a70 --filter $fl --robust $rb --stats $st
```
```powershell
$sl = (.\.venv\Scripts\rlab.exe select run or_667b73e2bf2d8a70 --filter $fl --robust $rb --stats $st --json | ConvertFrom-Json).sel_id
```
```powershell
$sl
```

### 6. 送ってほしいもの

1〜5 の表示をすべてそのまま貼ってください。**P8a（検証期間の実行）はまだ行わないでください。** P7 の結果を見て、事前登録のルールどおりに次の手順をお渡しします。

---

# v2（Discovery 6 年）の実機手順

条件: `docs/research/trend_v2/PREREGISTRATION.md`。変わったのは Discovery 期間（2008〜2013 年）だけです。
P1 → P7 を 1 回で続けて実行します。**同じ PowerShell の窓で、上から順に**実行してください。

### 0. 準備

```powershell
cd C:\work\repo\rlab
```
```powershell
git pull
```
```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "C:\MT5_verify\terminal64.exe" }
```
何も表示されなければ次へ。EA はコンパイルし直さないでください。

### 1. P1 単一テスト（2008〜2013 年の履歴の確認も兼ねる）

```powershell
.\.venv\Scripts\rlab.exe backtest run configs\requests\trend_ma_h4_v2_single.yaml
```
- 期待: `status: SUCCEEDED`、`warnings:` が空。初回は 6 年分の 1 分足をダウンロードするので時間がかかります。
- **`DATA_COVERAGE`、`QUARANTINED`、`FAILED`、または warnings に何か出たら、ここで止めて表示を送ってください。**
続けて、同じ単一テストの ID を変数に入れます（実行済みなので MT5 は起動せず、何も増えません）。
```powershell
$rn = (.\.venv\Scripts\rlab.exe backtest run configs\requests\trend_ma_h4_v2_single.yaml --json | ConvertFrom-Json).run_id
```
```powershell
.\.venv\Scripts\rlab.exe metrics show $rn
```

### 2. P3 最適化（25 パス）

```powershell
.\.venv\Scripts\rlab.exe optimize run configs\studies\trend_ma_h4_v2.yaml
```
```powershell
$or = (.\.venv\Scripts\rlab.exe optimize run configs\studies\trend_ma_h4_v2.yaml --json | ConvertFrom-Json).round_id
```
```powershell
.\.venv\Scripts\rlab.exe optimize show $or
```
- 期待: `passes_stored=25`、`remote 0`・`cloud 0`、`trials_in_family: 1094`。

### 3. P4 足切り

```powershell
.\.venv\Scripts\rlab.exe filter run $or --profile swing_trend
```
```powershell
$fl = (.\.venv\Scripts\rlab.exe filter run $or --profile swing_trend --json | ConvertFrom-Json).filter_id
```

### 4. P5 ロバストネス

```powershell
.\.venv\Scripts\rlab.exe robust run $or --filter $fl
```
```powershell
$rb = (.\.venv\Scripts\rlab.exe robust run $or --filter $fl --json | ConvertFrom-Json).robust_id
```
```powershell
.\.venv\Scripts\rlab.exe robust map $rb --metric score
```
```powershell
.\.venv\Scripts\rlab.exe robust map $rb --metric robust
```

### 5. P6 統計評価（シード 20261016）

```powershell
.\.venv\Scripts\rlab.exe stats run $or --filter $fl --seed 20261016
```
```powershell
$st = (.\.venv\Scripts\rlab.exe stats run $or --filter $fl --seed 20261016 --json | ConvertFrom-Json).stat_id
```

### 6. P7 選抜

```powershell
.\.venv\Scripts\rlab.exe select run $or --filter $fl --robust $rb --stats $st
```

### 7. 送ってほしいもの

1〜6 の表示をすべて貼ってください。**P8a はまだ行わないでください。**
