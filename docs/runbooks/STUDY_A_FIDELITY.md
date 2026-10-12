# 研究 A: EA との売買一致確認の手順（Windows・MT5）

目的: RL_TSMom（本物の EA）と、研究 A の Python の模型（T1 型）が、**同じ合成価格の上で同じ売買をし、毎日のエクイティと強制決済（ロスカット）まで一致するか**を確かめる（`docs/research/study_a/SPEC.md` §3・§12、照合の定義 t2）。
これに合格するまで、研究 A の正式な校正と本測定は始めない（コマンドの側でも拒否されます）。

**役割分担**: MT5 での実行は利用者が行います。Claude は手順とツールを用意し、添付された `fidelity_t1.json`（テスターの生データ入り）を解析して合否を判定します。不一致があれば原因を特定し、**直すのは模型（Python）の側で、EA は変えません**。模型を直した後は、同じ生データで再判定できるので（`rlab studya fidelity-check`）、MT5 をもう一度動かす必要は原則ありません。

**1 つの枠に 1 行**です。上から順に実行してください。所要時間の目安は 15〜30 分です。

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

## 2. 取り込みスクリプトのコピーとコンパイル

（RL_TSMom は前回コンパイル済みなので、そのまま使います）
```powershell
.\.venv\Scripts\python.exe scripts\install_eas.py --script RL_ImportT1
```
- 成功: `RL_ImportT1: Result: 0 errors` と `all compiled`。（今回のスクリプトは v1.01。口座のロスカット設定も書き出すように変わったので、**必ずコンパイルし直してください**）

## 3. 合成価格のファイルを書き出す

```powershell
.\.venv\Scripts\rlab.exe studya fidelity-export
```
- 成功: `H0: EURUSD_RLT1 …` と `E1: EURUSD_RLT2 …` の 2 行（それぞれ約 58 万本の 1 分足）。

## 4. MT5 でスクリプトを 1 回実行する

1. `C:\MT5_verify\terminal64.exe` を起動する（いつものポータブル版。デモ口座）。
2. 左の「ナビゲーター」→「スクリプト」→「RobustLab」→ **RL_ImportT1** を、どれかのチャートにドラッグする。
3. 入力の画面はそのまま「OK」。
4. 下の「エキスパート」タブに、次の行が出ることを確認する。
   - `RL_ImportT1: EURUSD_RLT1 … swap long 0.0000 short 0.0000 …`
   - `RL_ImportT1: EURUSD_RLT1 read … M1 bars, CustomRatesReplace returned …`（2 つの数が同じ）
   - `EURUSD_RLT2` についても同じ 2 行
   - `RL_ImportT1: account stop-out ACCOUNT_STOPOUT_MODE_PERCENT call … so 50.00, leverage … -> RL_T1_account.json`
     （ロスカットの方式と水準。口座番号・名義・サーバー名は書き出しません）
   - 最後に **`RL_ImportT1: done (EURUSD_RLT1 OK, EURUSD_RLT2 OK, settings OK)`**
5. **MT5 を閉じる**（次の手順で rlab が MT5 を自動で起動するため）。

`FAILED` が出たら、そこで止めて「エキスパート」タブの RL_ImportT1 の行を貼ってください。

## 5. 15 本の単一テストと照合（1 行）

```powershell
.\.venv\Scripts\rlab.exe studya fidelity-run
```
- MT5 が 15 回開いて閉じます（画面に触らない）。内訳は、通常の 12 本（資金 10,000 USD）と、**ロスカット専用の 3 本**（テスト用に資金を 200〜300 USD に下げたもの。ロットと EA の売買ルールは同じ）。結果は専用のワークスペース `workspace_studya_fid` に入ります（Git には入りません）。
- 最後の行が **`=== fidelity t2: MATCH (all 15 tests + settings) ===`** なら合格。
- `NOT MATCHED` の場合も、そのまま次へ（ファイルに原因の手がかりが入っています）。

## 6. 送ってほしいもの

`docs\research\study_a\results\fidelity_t1.json` を **1 ファイルだけ** 添付してください（約定・日次の残高とエクイティ・銘柄と口座の証拠金設定だけで、口座番号・名義・サーバー名・IP は含まれません）。
合わなかった場合も、このファイルに「どの約定が、どう違ったか」が入っているので、原因を調べられます（直すのは模型の側で、EA は変えません。SPEC §3.4）。
