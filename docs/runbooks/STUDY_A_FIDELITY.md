# 研究 A: EA との売買一致確認の手順（Windows・MT5）

目的: RL_TSMom（本物の EA）と、研究 A の Python の模型（T1 型）が、**同じ合成価格の上で同じ売買をするか**を確かめる（`docs/research/study_a/SPEC.md` §3）。
これに合格するまで、研究 A の本測定は始めない。

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
- 成功: `RL_ImportT1: Result: 0 errors` と `all compiled`。

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
   - 最後に **`RL_ImportT1: done (EURUSD_RLT1 OK, EURUSD_RLT2 OK)`**
5. **MT5 を閉じる**（次の手順で rlab が MT5 を自動で起動するため）。

`FAILED` が出たら、そこで止めて「エキスパート」タブの RL_ImportT1 の行を貼ってください。

## 5. 12 本の単一テストと照合（1 行）

```powershell
.\.venv\Scripts\rlab.exe studya fidelity-run
```
- MT5 が 12 回開いて閉じます（画面に触らない）。結果は専用のワークスペース `workspace_studya_fid` に入ります（Git には入りません）。
- 最後の行が **`=== fidelity t1: MATCH (all 12 tests) ===`** なら合格。
- `NOT MATCHED` の場合も、そのまま次へ。

## 6. 送ってほしいもの

`docs\research\study_a\results\fidelity_t1.json` を **1 ファイルだけ** 添付してください（口座番号や IP は含まれません）。
合わなかった場合も、このファイルに「どの約定が、どう違ったか」が入っているので、原因を調べられます（直すのは模型の側で、EA は変えません。SPEC §3.4）。
