# P3 実機確認の記録

確認項目は `docs/plans/P3_PLAN.md` §5。

## 実行環境と結果の概要（2026-10-10）

- Windows 11（論理 CPU 12）、MT5 build 6251（ポータブル `C:\MT5_verify`）、MetaQuotes-Demo、UI 言語は日本語
- `verify_p3.ps1` v1、EURUSD H1、2023-01-02〜2023-04-03、Model=1（M1 OHLC）、グリッド InpFast 5/10/15 × InpSlow 20/30/40/50 = 12 パス
- コンパイル: 0 errors, 0 warnings（MetaEditor の終了コードは成功でも 1。終了コードは判定に使わない）
- 全体の所要時間: 約 2 分 30 秒

| 実行 | 状態 | Frames | エージェントのファイル | XML | 所要 |
|---|---|---|---|---|---|
| O1 | DONE | 12 | 12 | あり | 26 秒 |
| O2（キャッシュを残して O1 を再実行） | PARTIAL | 0 | 0 | あり（O1 と同一） | 25 秒 |
| O3（キャッシュをどけて O1 を再実行） | DONE | 12 | 12 | あり（O1 と同一） | 26 秒 |
| O4（1 フレーム 20,145 double ≈ 161 KB、`OptimizationCriterion=6`） | DONE | 12 | 12 | あり | 26 秒 |
| S1（単一テスト Fast=10, Slow=30） | DONE | — | 1 | HTML | 27 秒 |

## 項目ごとの結果

| # | 結果 | 確認レベル |
|---|---|---|
| X1 | ✅ `Optimization=1` の ini で完全グリッド最適化が始まり、`ShutdownTerminal=1` で終了（終了コード 0） | A |
| X2 | 端末ログ（`logs\`）には `automatic testing started` だけが出て、**単一テストの成功行 `last test passed ...` は最適化では出ない**。テスターログ（`Tester\logs\`）に次が出る: `complete optimization started` → `optimization finished, total passes 12` → `Statistics ... 12 frames (15.61 Kb total, 1332 bytes per frame) received` → `Statistics local 12 tasks (100%), remote 0 tasks (0%), cloud 0 tasks (0%)` → `12 new records saved to cache file '...'` | A |
| X3 | XML は端末フォルダ直下の `<Report>.xml`、**UTF-8（BOM なし）**、SpreadsheetML、シート名 `Tester Optimizator Results`。**列名は英語**（UI は日本語で、同じ実行の HTML レポートは日本語）。列: `Pass, Result, Profit, Expected Payoff, Profit Factor, Recovery Factor, Sharpe Ratio, Custom, Equity DD %, Trades` ＋ **最適化した入力だけ**（固定した入力は出ない）。行数 = 12（＋見出し）。**行は Result の降順**（パス番号順ではない）。`DocumentProperties` に EA・銘柄・期間、サーバ、初期資金、レバレッジ | A |
| X4 | ✅ 12/12 フレームを受信。重複なし、`FrameFirst` からの再走査でも 12。今回はすべて `OnTesterPass` で受信し、`OnTesterDeinit` での遅延回収は 0。**`OnTesterPass` の 8 回の呼び出しで 12 フレーム**（1 回で複数届く → ループで読み切る必要がある）。**到着順はパス番号順ではない**（5 が 4 より先）。`FrameInputs` でパラメータを取得できた。集計側のインスタンスは `MQL_FRAME_MODE`=true、`MQL_TESTER`=false。`FILE_COMMON` と端末の `MQL5\Files` の両方に書けた。パス番号は最初の入力が内側のループ（pass = i_fast + 3 × i_slow） | A |
| X5 | ✅ 1 フレーム 20,145 double（約 161 KB）× 12 を受信。日次データの必要量（10 年で約 40 KB）を十分に上回る | A |
| X6 | **同じ EA・入力・期間の 2 回目はキャッシュから返り、パスは実行されず、Frames も届かない**。テスターログに `cache file '...' contains 12 records` / `optimization already processed, total passes 12` / `reading of 12 result records from cache...`。XML は O1 と同一の内容で出力される（気づかないと成功に見える）。キャッシュ（`Tester\cache\<EA>.<銘柄>.<時間足>.<開始>.<終了>.<数字>.<ハッシュ>.opt`）をどければ再実行される。ハッシュは入力の範囲で変わる（O4 は別ファイル）。単一テストも `.tst` キャッシュを作るが、再実行される（P1 の W 項目と同じ） | A |
| X7 | ✅ テスターログに `Local network farm switched off`、`Cloud servers switched off`、`local 12 tasks (100%), remote 0 tasks (0%), cloud 0 tasks (0%)`。エージェント側のログに `cloud network mode is off`。使われたのはローカルエージェント 12 個（端末フォルダ内の `Tester\Agent-<ローカルアドレス>-3000`〜`3011`、論理 CPU 数と同じ） | A |
| X8 | ✅ M1 OHLC で、単一テスト S1 と最適化のパス 4（Fast=10, Slow=30）が完全一致（純損益 38.00、取引 49、約定 98、総利益/総損失、残高 DD・エクイティ DD、PF、RF、シャープ、最終エクイティ）。Real ticks は E2E で確認する | A（M1 OHLC） |
| X9 | ✅ `\|\|Y` の入力が最適化軸になり（XML の列とグリッド）、`\|\|N` の入力は固定（XML に出ない。O4 の `InpFramePad=20000` はフレームの大きさに反映）。文字列入力 `RL_RunTag` も固定値で全パスに渡った | A |
| X10 | エージェント 12 個。1 パス 0.146〜0.214 秒（3 か月・M1 OHLC）、最適化本体 1 秒。1 回の実行は端末の起動・ログイン・終了を含めて約 26 秒（うち 20 秒はスクリプトの待機） | A |
| 追加 | **`Custom` 列（`OnTester` の戻り値）は基準の指定によらず常に出る**。`Result` は既定では最終残高（10038.00）、`OptimizationCriterion=6` ではカスタム値（15050 など）で、並び順もそれに従う | A |
| 再現性 | O1・O3・O4 の日次エクイティ（12 パス × 65 日）は、パス番号順に並べ替えると完全一致。XML も O1・O2・O3 で同一 | A |

## 気づいた問題

- **IPv6 アドレスの伏せ字漏れ**: 端末ログの `previous successful authorization performed from <IPv6>` が、v1 のスクリプトでは伏せ字になっていなかった（IPv4 だけ対応していた）。結果の zip は Claude のセッションにだけ送られ、リポジトリにはコミットしていない。P1・P3 のスクリプトに IPv6 と `performed from ...` の伏せ字を追加した。製品側の `workspace/` のログは従来どおり CLI で表示せず、コミットしない（F8）。

## 仕様への反映（提案。承認後に確定）

`docs/plans/P3_PLAN.md` §10 を参照。
