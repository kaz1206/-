# P1 実機 E2E 手順（Windows）

P1 の完了条件（`docs/plans/P1_PLAN.md` §13 の 3〜8）を Windows 実機で確認する手順です。
実機確認で使った `C:\MT5_verify`（ポータブル版・デモ口座・パスワード保存済み・アルゴリズム取引オフ）をそのまま使います。

## 準備（初回のみ）

1. **Python 3.12 または 3.13** をインストールします（python.org のインストーラ）。
2. リポジトリ（ブランチ `claude/mt5-ea-robustness-framework-jz17ar`）を取得し、そのフォルダで PowerShell を開きます。
3. 依存関係を入れます（ロックファイルどおりの版が入ります）:
   ```
   py -3.13 -m pip install uv
   py -3.13 -m uv sync
   ```
4. MQL5 のファイルを端末にコピーします:
   - `mql5\Include\RobustLab\` → `C:\MT5_verify\MQL5\Include\RobustLab\`
   - `mql5\Experts\RobustLab\` → `C:\MT5_verify\MQL5\Experts\RobustLab\`
5. `C:\MT5_verify\metaeditor64.exe` で `MQL5\Experts\RobustLab\RL_SmokeTest.mq5` を開き、**F7** でコンパイルします。
   - **エラーが出たら、そこで止めてエラー表示を送ってください**（Telemetry は初めてのコンパイルです）。
6. `configs\terminal.example.yaml` を `configs\terminal.yaml` にコピーし、次の 2 行を自分の環境に合わせます:
   - `terminal_path: C:/MT5_verify/terminal64.exe`
   - `common_files_dir: C:/Users/<ユーザー名>/AppData/Roaming/MetaQuotes/Terminal/Common/Files`
7. MT5 を閉じます（起動中だと実行は拒否されます）。

## 確認 1: 単一テストの成功と保存（完了条件 3・7・8）

```
py -3.13 -m uv run rlab backtest run configs\requests\smoketest_eurusd_h1.yaml
```
- 期待: `status: SUCCEEDED`、終了コード 0。`deals`、`net_profit_mt5` と `net_profit_sum`（一致するはず）、`observed_build` が表示されます。
- 2023 年 1 年分のリアルティックを使うため、初回は数分〜数十分かかることがあります（タイムアウトは 60 分）。
- 表示された `run_id` を控えてください。

## 確認 2: 再現性（完了条件 4）

```
py -3.13 -m uv run rlab backtest run configs\requests\smoketest_eurusd_h1.yaml --rerun
```
- 期待: `warnings` に `REPRO_MATCH`。
- `REPRO_NOT_COMPARABLE_BUILD_CHANGED` の場合は、MT5 が自動更新でビルドを変えています（F6）。もう一度 `--rerun` してください。
- `REPRO_MISMATCH` の場合は、そのまま結果を送ってください（原因調査が必要です）。

## 確認 3: Holdout の拒否（完了条件 5）

```
py -3.13 -m uv run rlab backtest run configs\requests\holdout_check_eurusd_h1.yaml
```
- 期待: `status: GUARD_REJECTED`、終了コード 3、MT5 は起動しない。

## 確認 4（任意）: 異常終了が成功扱いにならないこと（完了条件 6）

1. `--rerun` 付きで確認 1 のコマンドを実行し、MT5 のウィンドウが開いたら、タスクマネージャーで `terminal64.exe` を終了します。
2. 期待: `status` が `SUCCEEDED` 以外（`TELEMETRY_MISSING` など）、終了コード 2。

## 結果を送る

次の 2 つの出力をコピーして送ってください。ログの中身は表示されないので、口座番号や IP アドレスは含まれません（F8）。
```
py -3.13 -m uv run rlab backtest show <run_id>
py -3.13 -m uv run rlab jobs list
```
`workspace\` フォルダは送らないでください（ログに口座番号と接続元 IP が含まれます）。
