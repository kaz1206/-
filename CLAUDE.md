# CLAUDE.md — MT5 EA Robustness Lab

MT5 Strategy Tester を使い、EA のロバスト性・過学習耐性・DD/破産リスクを評価し、資金配分まで決める研究基盤。
P1〜P7 は完了（P7 まで実機・実データで確認合格）。P8a（複数期間検証）も完了（実機は診断モードで確認）。P8b（OOS）も完了（練習用ワークスペースで一度きりの実行・封印・再実行の拒否を実機確認）。**本番の Holdout は封印したまま。本物の EA で P8a の合格者が出るまで使わない。** P9（リスクと資金配分）も完了（実機は診断モードで確認。通貨換算の欠陥を実機で発見・修正、C81）。P10（帰無仮説テスト）も完了（判定 A1 0.30%・A2 1.40%・A3 2.80%、すべて上側限界 ≤ 5%。実機の忠実度チェックは照合 v2 で約定 99/99・損益 49/49 一致）。設計書 §20 の MVP（P1〜P10）はここまで。

## 主要ドキュメント

- 使い方（初心者向けマニュアル）: `docs/HOW_TO_USE.md`
- 設計書: `docs/ARCHITECTURE.md`（`[S-xxx]` = 情報源 ID、`(C#)` = 設計変更番号）
- 情報源レジストリ: `docs/research/SOURCES.md`
- 手法評価・MT5 仕様の確認結果・実機確認チェックリスト: `docs/research/FINDINGS.md`
- 設計変更履歴: `docs/DESIGN_CHANGELOG.md`
- P10 計画（完了、C82〜C86）: `docs/plans/P10_PLAN.md`、設定: `configs/null_archetypes.yaml`、試算: `docs/research/p10_simulations/`、判定の記録: `docs/research/p10_validation/`
- P9 計画（完了、C77〜C81）: `docs/plans/P9_PLAN.md`、プロファイル: `configs/risk_profiles.yaml`、事前シミュレーション: `docs/research/p9_simulations/`、再検証: `docs/research/p9_validation/`
- P8b 計画（完了、C74〜C76）: `docs/plans/P8B_PLAN.md`、練習用の設定: `configs/rehearsal/`
- P8 計画（P8a 完了、C71〜C73）: `docs/plans/P8_PLAN.md`、検証計画: `configs/validation_plans/`、追加コスト: `configs/cost_stress.yaml`
- P7 計画（完了、C68〜C70）: `docs/plans/P7_PLAN.md`、検証結果: `docs/research/p7_validation/RESULTS.md`、プロファイル: `configs/selection_profiles.yaml`
- P6 計画（完了、C61〜C67）: `docs/plans/P6_PLAN.md`、検証結果: `docs/research/p6_validation/RESULTS.md`
- P5 計画（完了、C56〜C60）: `docs/plans/P5_PLAN.md`
- P4 計画（完了、C51〜C55）: `docs/plans/P4_PLAN.md`、PSR の入力定義: `docs/metrics_definitions.md`、プロファイル: `configs/strategy_profiles.yaml`
- P3 計画（完了、C40〜C50）: `docs/plans/P3_PLAN.md`、実機確認: `verification/p3/RECORD.md`、E2E 手順: `docs/runbooks/P3_E2E.md`
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
- DSR と各種補正値は警告・順位づけにだけ使い、単独で合否を決めない（C62）。P6 の値はすべて `DISCOVERY_IN_SAMPLE` で、OOS の証拠として扱わない。
- 偶然の合格率が基準を満たさない依存構造（AR(2) など）は既知の限界として結果に明示し、補正で安全性が保証されたとは扱わない。
- P8 の OOS 検証では、期間の事前固定・結果閲覧後のパラメータ変更による汚染管理（`HOLDOUT_CONTAMINATED`）・再試行の記録を徹底する。P10 は実際の EA の構造を反映した帰無仮説で行い、前提・限界・偶然の合格率を記録する。
- 事前登録した合格基準は結果を見た後に緩めない。未達は未達として記録する。
- P8a: 検証計画は実行前にロックし、変更は新しい計画（`VALIDATION_REVISED`）。結果を見た期間は露出台帳に残り、それより後に作ったラウンドの検証には使えない。統計ゲートの不合格は「証拠不足」でエッジなしではない。診断モードの結果は判定に使わない。
- P8b: マニフェストはワークスペースに 1 つ、実行は一度きりで最初のジョブ前に Holdout を燃やす。OOS の PASS は「事前の基準に反証されなかった」、FAIL は「エッジ消失の証明ではない」。シミュレーションの数字は実際の EA の誤判定率ではない。
- P9: ロットを出すのは P8a で PASS（診断ではない計画）の候補だけ。それ以外は DIAGNOSTIC（1 ロットあたりの指標のみ、ロット・判定なし）。P8b FAIL は配分しない。練習用ワークスペースの OOS は信頼度を上げない。安全ロットは DD・破産・ストップアウトの 3 制約をすべての制約用シナリオ（S0・S1b・S2〜S6・S63、生き残りは SC も）で同時に満たす最大値（95% 上側信頼限界）。ロットは常に切り捨て、最小ロット未満は `INFEASIBLE_MIN_LOT`。切り上げ・制約の緩和の経路を作らない。S1a（含み損なしの取引シャッフル）は参考指標。P9 で安全に見えても優位性の証拠ではない。恒久的な体制変化への耐性は未検証（D5 の 1.5 倍の試算結果とは分けて表示）。Holdout のデータは使わない。バックテストの通貨と口座の通貨が違うときはスナップショット時点のレートで換算し、換算できなければ `CURRENCY_MISMATCH`（C81）。
- P10: 帰無仮説は EA の仕組みを予測できる構造のない価格で再実行する（損益のシャッフルは使わない）。判定は型ごとに 1,000 ラウンド・固定シードで 1 回だけ、上側限界 ≤ 5%。S1 不合格の実行は無効、S1 の通過は H0 の証明ではない。偶然の合格率と合格後の破綻リスク（マーチン）は別に扱う。
- P7 で選ばれたことはエッジの証拠ではない（エッジなしの合成データでも 73% の回で何かが選ばれる）。P7 は P8・P10 に進める候補を絞る段階。P7 の判定は `filter_id` を空にして `sel_id` で結ぶ。

## 実行環境メモ

- MT5 と MetaTrader5 Python パッケージは **Windows 専用**。Python は 3.12/3.13。
- 統計・評価の中核ロジックは MT5 に依存させず、Linux でも単体テストできる構成にする。
- Claude Code のクラウド環境では `mql5.com` / `metatrader5.com` 等へのアクセスがブロックされることがある。その場合は確認レベルを B/C として記録し、`docs/research/FINDINGS.md` §3 の実機確認チェックリストに回す。

## コマンド

- 依存関係: `uv sync`（ロックファイル `uv.lock`。Python 3.12/3.13）
- テスト: `uv run pytest`（Linux で全テストが動く。MT5 は偽の端末 `tests/integration/fake_terminal.py` で代替。P6 のモンテカルロ検証 `tests/validation/` を含めて 3〜4 分。Windows では偽の端末が動かないため `tests/integration/` は skip になる）
- CLI: `uv run rlab backtest run <request.yaml> [--rerun]` / `rlab backtest show <run_id>` / `rlab jobs list` / `rlab metrics compute <job_id|--all>` / `rlab metrics show <run_id>`
- CLI（P4〜P6）: `rlab filter run <round_id> --profile <name>` / `rlab why-not <candidate_id>` / `rlab robust run <round_id> --filter <filter_id>` / `rlab stats run <round_id> --filter <filter_id> [--candidate <cd>] [--reps 2000] [--seed N]` / `rlab stats show <stat_id> [--candidate <cd>]`
- CLI（P3）: `rlab optimize run <study.yaml> [--rerun]` / `rlab optimize show <round_id>` / `rlab optimize pass <round_id> <candidate_id>` / `rlab trials show <strategy_family>`
- CLI（P4）: `rlab filter run <round_id> --profile <name>` / `rlab filter show <filter_id>` / `rlab why-not <candidate_id> [--filter <filter_id>]`
- CLI（P7）: `rlab select run <round_id> --filter <fl> --robust <rb> --stats <st> [--profile default]` / `rlab select show <sel_id> [--candidate <cd>]`
- CLI（P8a）: `rlab validate lock <sel_id> --plan <yaml> [--diagnostic --candidate <cd>]` / `rlab validate run <plan_id>` / `rlab validate show <plan_id> [--candidate <cd>]`（閲覧は記録される）
- CLI（P8b）: `rlab oos lock <plan_id> [--rehearsal --candidate <cd>]` / `rlab oos run <manifest_id> --i-understand-this-is-final` / `rlab oos show <manifest_id>`（練習は `--workspace workspace_rehearsal --partition configs/rehearsal/data_partition.yaml`）
- CLI（P10）: `rlab null run --archetype A1|A2|A3 --mode judgement|reproduction|power` / `rlab null show <null_id>`（判定は設定に固定、2 回目は拒否）/ 忠実度チェック `rlab null fidelity-export` → `mql5/Scripts/RobustLab/RL_ImportH0.mq5`（カスタム銘柄 `EURUSD_RLH0`）→ `rlab backtest run configs/requests/p10_fidelity_h4.yaml --workspace workspace_p10fid` → `rlab null fidelity-compare --workspace workspace_p10fid`
- CLI（P9）: `rlab risk snapshot --symbol <sym> --out <json>`（Windows のみ。MT5 を起動・ログインして実行。口座番号・名義・サーバー名は保存しない）/ `rlab risk run <plan_id> --snapshot <json> [--equity N] [--candidate <cd>]` / `rlab risk show <risk_id> [--candidate <cd>]`
- CLI（P5）: `rlab robust run <round_id> --filter <filter_id>` / `rlab robust show <robust_id> [--candidate <cd>]` / `rlab robust map <robust_id> [--metric score|robust]`
- 終了コード: 0 成功、1 設定・入力エラー、2 実行時の失敗、3 Holdout Guard による拒否

## 実装上の約束（P1）

- `verification/` は実機確認専用。P1 本体（`src/`、`mql5/`）から参照しない。
- `.set` は UTF-16LE＋BOM、ini は ASCII（実機で確認した形式。変更する場合は実機確認から）。
- 成功の判定は「端末ログの成功行＋完了マーカー＋整合性チェック」のすべて。どれかが欠けたら SUCCEEDED にしない。
- 最適化の成功は「テスターログの完了行（期待パス数）＋エージェント内訳 remote 0・cloud 0＋Frames の完了マーカーと全パス＋XML との照合」のすべて（C46〜C49）。最適化の前に対象 EA の `.opt` キャッシュを削除する。
- EA は最適化で `INIT_PARAMETERS_INCORRECT` を返さない（そのパスはフレームを送らず欠落扱いになる）。無効な組合せは「取引しないパス」として走らせる。
- MT5 の更新は研究のひと区切りの合間に手動で行い、比較の途中では更新しない（保留中の更新はジョブ中に自動適用され、再現性の比較が「比較対象外」になる）。
- P4 の足切りはロットに依存しない基準だけ（DD の上限は P9、C51）。証拠金維持率は「ラウンドの標準条件での値」で、安全性の判断ではない（C52）。PSR の閾値は利益の確率ではなく、多重検定の補正は DSR（P6）で行う（C54）。
- P5 は評価値だけを出し、合否は決めない（判定は P7）。−∞ の原因（`score_status`）と島がない理由（`region_status`・`regions_reason`）を必ず保存する（C59）。
- `workspace/` のログには口座番号と IP が含まれる。CLI でログ本文を表示しない。リポジトリにコミットしない。
