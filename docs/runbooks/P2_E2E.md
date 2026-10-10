# P2 実機確認の手順（Windows）

P2 の実機確認（`docs/plans/P2_PLAN.md` §5 の V2-1〜V2-5）を行う手順です。
**1 つの枠に 1 行**です。1 行ずつ貼り付けて Enter を押し、結果を確認してから次へ進んでください。

## 1. 最新版の取得（マウス操作）

GitHub でブランチ `claude/mt5-ea-robustness-framework-jz17ar` を開き、「Code」→「Download ZIP」でダウンロードして、`C:\work\repo\p2` に展開します。

## 2. 設定と記録の引き継ぎ

```powershell
$old = "C:\work\repo\p1-v2\--claude-mt5-ea-robustness-framework-jz17ar"
```
```powershell
$new = "C:\work\repo\p2\--claude-mt5-ea-robustness-framework-jz17ar"
```
```powershell
Test-Path "$new\pyproject.toml"
```
（`True` なら次へ。`False` なら `Get-ChildItem C:\work\repo\p2` の表示を送ってください）
```powershell
Copy-Item "$old\configs\terminal.yaml" "$new\configs\"
```
```powershell
Copy-Item -Recurse "$old\workspace" "$new\"
```
```powershell
cd $new
```
```powershell
py -3.13 -m uv sync
```

## 3. Telemetry v2 のコピーとコンパイル（V2-1）

変更されたのは `Telemetry.mqh` だけです（EA 本体は同じ）。
```powershell
Copy-Item -Force .\mql5\Include\RobustLab\Telemetry.mqh C:\MT5_verify\MQL5\Include\RobustLab\
```
```powershell
$me = "C:\MT5_verify\metaeditor64.exe"
```
```powershell
$src = "C:\MT5_verify\MQL5\Experts\RobustLab\RL_SmokeTest.mq5"
```
```powershell
$log = "$PWD\compile_smoke.log"
```
```powershell
Start-Process -Wait $me -ArgumentList "/portable", "/compile:`"$src`"", "/log:`"$log`""
```
```powershell
Get-Content $log -Encoding Unicode | Select-String "Result|error"
```
`Result: 0 errors` なら成功。`error` の行が出たら、**そこで止めて**表示を送ってください。

## 4. 実行（V2-2〜V2-5）

```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "C:\MT5_verify\terminal64.exe" }
```
（何も表示されなければ次へ）
```powershell
py -3.13 -m uv run rlab backtest run configs\requests\smoketest_eurusd_h1.yaml
```
- EA の版（Telemetry）が変わったので、新しい `run_id` で `attempt: 1` になります。
- 期待: `status: SUCCEEDED`、`metric_checks` に `MISMATCH` がないこと。

## 5. 結果の表示

`<run_id>` は 4 で表示されたものに置き換えてください。
```powershell
py -3.13 -m uv run rlab metrics show <run_id>
```
P1 で保存済みのジョブ（Telemetry v1）にも指標を計算します（完了条件 5）。
```powershell
py -3.13 -m uv run rlab metrics compute --all
```

4 と 5 の表示を、すべて送ってください。ログの中身は表示されないので、口座番号や IP アドレスは含まれません。
