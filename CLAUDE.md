# CLAUDE.md — MT5 EA Robustness Lab

MT5 Strategy Tester を使い、EA のロバスト性・過学習耐性・DD/破産リスクを評価し、資金配分まで決める研究基盤。
P1（単一バックテストの安全な実行と保存）と P2（Telemetry v2・指標の自前計算 MetricEngine m2）は完了（2026-10-10 実機確認合格）。

## 主要ドキュメント

- 設計書: `docs/ARCHITECTURE.md`（`[S-xxx]` = 情報源 ID、`(C#)` = 設計変更番号）
- 情報源レジストリ: `docs/research/SOURCES.md`
- 手法評価・MT5 仕様の確認結果・実機確認チェックリスト: `docs/research/FINDINGS.md`
- 設計変更履歴: `docs/DESIGN_CHANGELOG.md`
- P3 計画（承認済み、C40〜C45）: `docs/plans/P3_PLAN.md`、実機確認: `verification/p3/`
- P1 計画（§15 が確定事項）: `docs/plans/P1_PLAN.md`、実機確認の記録: `verification/p1/RECORD.md`
- P2 計画: `docs/plans/P2_PLAN.md`、指標定義: `docs/metrics_definitions.md`

## 調査ポリシー（設計・実装の前に必ず守る）

1. **再発明しない。** 実装前に、リポジトリ内の既存コードと `docs/research/FINDINGS.md` を確認する。採用済みの既存実装:
   - Bootstrap・ブロック長・SPA・StepM → `arch`（バージョン固定）
   - PBO → R `pbo` 1.3.5 からの移植（数値一致テスト必須）
   - 金額・証拠金換算 → MetaTrader5 の `order_calc_profit` / `order_calc_margin`
   - CPCV が必要になった場合 → skfolio
2. **情報源の優先順位:**
   1. 公式ドキュメント（Anthropic/Claude Code、MetaTrader 5/MQL5、Python と主要ライブラリ）
   2. 原著論文・査読論文・著者の資料
   3. 公式 GitHub・Issue・リリースノート
   4. その他の技術記事・コミュニティ（一次情報で確認できない部分の補助のみ）
3. **API・仕様は記憶で決めない。** MT5 Strategy Tester、設定ファイル/コマンドライン、MetaTrader5 Python API、MQL5 Tester API、使用ライブラリの現行 API は、実装の直前に最新の公式情報で確認する。ライブラリのバージョンは PyPI で確認する。
4. **手法は 5 観点で評価してから採否を決める:** 何を測るか / どの仮定に依存するか / 今回の問題に適しているか / 他手法と重複しないか / 実装コストに見合うか。
   - 既存手法を無批判に採用しない。
   - 複数の手法があるときは「最新」より「検証済み・単純・保守しやすい」ものを選ぶ。
5. **記録する:**
   - 新しい情報源は `docs/research/SOURCES.md` に ID と確認レベル（A/B/C/V）付きで追加する。
   - 設計判断の変更は `docs/DESIGN_CHANGELOG.md` に「変更前 → 変更後・理由・根拠 ID」で追記する。
6. **独自の拡張は明示する。** 既存手法を本基盤向けに変えた箇所（例: CSCV の中で独自 Selector を回す）には「独自拡張」と書き、標準版と並べて報告する。

## 研究上の不変条件（コードでも守る）

- Holdout（OOS）期間のデータは、OOS ステージ以外のジョブ生成・データ読込で使わない（Holdout Guard を通す）。
- 検証期間ごとに再最適化しない。選ばれた候補のパラメータは固定する。
- 研究結果テーブル（candidate / run / evaluation / decision）は追記のみ（UPDATE・DELETE しない）。
- 判定（Decision）には必ず理由コードと、使ったコンポーネント名・バージョンを記録する。
- 試行回数は捨てた試行も含めて `trial_ledger` に記録する。
- 乱数を使う処理はシードを記録する。

## 実行環境メモ

- MT5 と MetaTrader5 Python パッケージは **Windows 専用**。Python は 3.12/3.13。
- 統計・評価の中核ロジックは MT5 に依存させず、Linux でも単体テストできる構成にする。
- Claude Code のクラウド環境では `mql5.com` / `metatrader5.com` 等へのアクセスがブロックされることがある。その場合は確認レベルを B/C として記録し、`docs/research/FINDINGS.md` §3 の実機確認チェックリストに回す。

## コマンド

- 依存関係: `uv sync`（ロックファイル `uv.lock`。Python 3.12/3.13）
- テスト: `uv run pytest`（Linux で全テストが動く。MT5 は偽の端末 `tests/integration/fake_terminal.py` で代替）
- CLI: `uv run rlab backtest run <request.yaml> [--rerun]` / `rlab backtest show <run_id>` / `rlab jobs list` / `rlab metrics compute <job_id|--all>` / `rlab metrics show <run_id>`
- 終了コード: 0 成功、1 設定・入力エラー、2 実行時の失敗、3 Holdout Guard による拒否

## 実装上の約束（P1）

- `verification/` は実機確認専用。P1 本体（`src/`、`mql5/`）から参照しない。
- `.set` は UTF-16LE＋BOM、ini は ASCII（実機で確認した形式。変更する場合は実機確認から）。
- 成功の判定は「端末ログの成功行＋完了マーカー＋整合性チェック」のすべて。どれかが欠けたら SUCCEEDED にしない。
- `workspace/` のログには口座番号と IP が含まれる。CLI でログ本文を表示しない。リポジトリにコミットしない。
