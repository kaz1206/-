# P3 実機確認: MT5 最適化の動きを確かめる

このフォルダは**実機確認専用**です。本体（`src/`、`mql5/`）とは分離されています。
確認項目は `docs/plans/P3_PLAN.md` §5（X1〜X10）、結果の記録は [RECORD.md](RECORD.md) です。

## やること（準備 3 分 ＋ 自動実行 10〜30 分）

前提: P1・P2 で使った `C:\MT5_verify`（デモ口座でログイン済み、「アルゴリズム取引」オフ）と、Git で取得した `C:\work\repo\rlab` があること。

1. 最新版を取得する（`C:\work\repo\rlab` で）
   ```powershell
   git pull
   ```
2. MT5 が開いていたら閉じる（「ファイル」→「終了」）
3. スクリプトを実行する
   ```powershell
   powershell -ExecutionPolicy Bypass -File "C:\work\repo\rlab\verification\p3\scripts\verify_p3.ps1" -TerminalDir "C:\MT5_verify"
   ```
4. 緑色で `Finished. Please send this file:` と出たら、表示された `results_<日時>.zip` を送る

実行中は MT5 が 5 回開いて閉じます。**MT5 の画面には触らないでください。** 最適化の間は CPU を全コア使うので、PC が重くなります。

## 何をしているか

| 実行 | 内容 | 確かめること |
|---|---|---|
| O1 | 12 パス（3 × 4）の完全グリッド最適化（M1 OHLC、2023 年 1〜3 月） | ini から最適化できるか、終了するか、ログの文言、XML の形式、Frames が全パス届くか |
| O2 | O1 をそのまま繰り返す（キャッシュは残す） | 2 回目はキャッシュから返るか、そのとき Frames は届くか |
| O3 | キャッシュをどけてから O1 を繰り返す | どければ再実行されるか |
| O4 | 1 パスあたり約 160 KB のフレーム＋カスタム最適化基準 | 大きなフレームを送れるか、XML の Result 列 |
| S1 | 単一テスト（Fast=10、Slow=30） | 最適化の同じパスと結果が一致するか |

## 安全性について

- 注文は Strategy Tester の中（`MQL_TESTER` が真）でだけ出します。最適化中に MT5 が端末側で動かす集計用のインスタンス（`MQL_FRAME_MODE`）は注文を出しません（`OnTick` が最初に戻ります）。通常のチャートでは起動を拒否します。
- ini で `UseLocal=1`、`UseRemote=0`、`UseCloud=0` を指定し、有料のクラウドエージェントを使わないようにしています。実際にローカルだけが使われたかは、結果のエージェント一覧とログで確認します。
- MT5 の最適化キャッシュ（`Tester\cache`）は**削除せず**、`C:\MT5_verify\Tester\cache_rlv_backup\<日時>\` に移動します。
- 結果の zip からは、口座番号・口座名・Windows のユーザー名・PC 名・IP アドレスを伏せ字にしています。

## 困ったとき

- `Compile did not produce RL_Verify_P3.ex5` と出た場合: 表示されたフォルダの中の `compile.log` を送ってください。
- 30 分以上進まない場合: スクリプトが自動で打ち切り、次の実行に進みます。最後の zip はそのまま送ってください。
