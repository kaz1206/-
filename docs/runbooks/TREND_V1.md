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
