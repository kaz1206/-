# P3 実機 E2E の手順（Windows）

P3 の完了条件（`docs/plans/P3_PLAN.md` §7）を実機で確認する手順です。
**1 つの枠に 1 行**です。1 行ずつ貼り付けて Enter を押し、結果を確認してから次へ進んでください。

所要時間の目安: 準備 5 分、最適化 20〜60 分（1,044 パス、Real ticks、2 年分。初回はティック履歴のダウンロードが入ります）。最適化の間は CPU を全コア使います。

## 1. 最新版の取得

```powershell
cd C:\work\repo\rlab
```
```powershell
git pull
```
```powershell
py -3.13 -m uv sync
```

## 2. EA と Telemetry v3 のコピーとコンパイル

`Telemetry.mqh`（最適化のフレーム送受信を追加）と `RL_SmokeTest.mq5`（テスターイベントを追加）の両方が変わっています。
```powershell
Copy-Item -Force .\mql5\Include\RobustLab\Telemetry.mqh C:\MT5_verify\MQL5\Include\RobustLab\
```
```powershell
Copy-Item -Force .\mql5\Experts\RobustLab\RL_SmokeTest.mq5 C:\MT5_verify\MQL5\Experts\RobustLab\
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
`Result: 0 errors` なら成功（MetaEditor の終了コードは成功でも 1 なので見ません）。`error` の行が出たら、**そこで止めて**表示を送ってください。

## 3. 最適化の実行

```powershell
Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "C:\MT5_verify\terminal64.exe" }
```
（何も表示されなければ次へ）
```powershell
py -3.13 -m uv run rlab optimize run configs\studies\rl_smoketest_grid.yaml
```
- 1,044 パスを 2 つのジョブ（580 パス ＋ 464 パス）に分けて順に実行します。MT5 が 2 回開いて閉じます。**画面には触らないでください。**
- 期待: `status: SUCCEEDED`、各ジョブの行が `passes=580/580`、`passes=464/464`。
- `round_id:` の値（`or_` で始まる）を控えてください。
- 赤字で `SAFETY:` と出た場合は、クラウドやリモートのエージェントが使われた可能性があります。**そこで止めて**表示を送ってください。

## 4. 結果の確認

`or_xxxxxxxxxxxxxxxx` を 3 で表示された `round_id` に置き換えてください。
```powershell
py -3.13 -m uv run rlab optimize show or_xxxxxxxxxxxxxxxx
```
- 期待: `passes_stored=1044`、`chunks_succeeded=2/2`、各ジョブの `agents=` が `remote 0`・`cloud 0`、`trials_in_family: 1044`。
```powershell
py -3.13 -m uv run rlab trials show RL_SmokeTest
```
```powershell
py -3.13 -m uv run rlab jobs list
```

## 5. 再現性の確認（完了条件 7）

同じ最適化をもう一度実行し、全パスの日次エクイティが一致することを確かめます（キャッシュは rlab が自動で削除します）。
```powershell
py -3.13 -m uv run rlab optimize run configs\studies\rl_smoketest_grid.yaml --rerun
```
- 期待: 各ジョブに `warning: REPRO_MATCH: ...`。試行回数（`trials show`）は 1,044 のまま増えません。

## 6. 単一テストとの一致（X8 の Real ticks 版）

最適化の 1 パス（Fast=12, Slow=50）と同じパラメータ・期間で単一テストを実行し、結果を比べます。パラメータが同じなら `candidate_id` も同じになります。
```powershell
py -3.13 -m uv run rlab backtest run configs\requests\x8_check_eurusd_h1.yaml
```
表示された `candidate_id:`（`cd_` で始まる）を控えてください。次の行の `or_xxxxxxxxxxxxxxxx` を 3 の `round_id` に、`cd_xxxxxxxxxxxxxxxx` を今の `candidate_id` に置き換えます。
```powershell
py -3.13 -m uv run rlab optimize pass or_xxxxxxxxxxxxxxxx cd_xxxxxxxxxxxxxxxx
```
- 期待: 単一テストの `net_profit_mt5` と `deals` が、パスの `net_profit` と `deals_mt5` に一致する。

## 7. 送ってほしいもの

3〜6 の画面表示をそのまま貼り付けてください。ログの本文は表示されないので、口座番号などが画面に出ることはありません。
