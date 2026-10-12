# 古典手法 v1 の実機手順（Windows）

条件: `docs/research/classics/PREREGISTRATION.md`。1 つの枠に 1 行です。上から順に実行してください。

## 1. 最新版の取得

```powershell
cd C:\work\repo\rlab
```
```powershell
git pull
```

## 2. 3 本の EA をコピーしてコンパイル（1 行）

```powershell
.\.venv\Scripts\python.exe scripts\install_eas.py RL_Donchian RL_TSMom RL_BollingerMR
```
- 成功: 最後に `all compiled`、各 EA に `Result: 0 errors` と `OK`。
- `COMPILE FAILED` が出たら、そこで止めて表示を貼ってください（EA を直します）。

## 3. P1〜P7 を 3 研究続けて実行（1 行）

MT5 が起動していないことを確認してから実行します（MT5 が何度も開いて閉じます。画面には触らないでください）。

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py configs\pipelines\classic_donchian_h4_v1.yaml configs\pipelines\classic_tsmom_h4_v1.yaml configs\pipelines\classic_bbmr_h4_v1.yaml
```
- 画面にも表示されますが、全部が 1 つのファイルに保存されます: `runs\batch3_（日時）.txt`
- 最後の行が `# summary: classic_donchian_h4_v1=DONE, classic_tsmom_h4_v1=DONE, classic_bbmr_h4_v1=DONE` なら全部完了。`STOPPED` があれば、その研究だけ途中で止まっています（理由はファイルに書いてあります）。

## 4. 送ってほしいもの

`runs` フォルダにできた `batch3_…txt` を **1 ファイルだけ** 添付してください。コピー＆貼り付けは不要です。
ファイルに口座番号や IP は含まれません（rlab はログ本文を表示しないため）。
