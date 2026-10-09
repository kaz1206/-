# 設計変更履歴

## v0.2（2026-10-08）: 一次情報・原著研究レビューの反映

調査の詳細は [research/FINDINGS.md](research/FINDINGS.md)、情報源は [research/SOURCES.md](research/SOURCES.md)。

| # | 対象（ARCHITECTURE.md） | 変更前（v0.1） | 変更後（v0.2） | 理由 | 根拠 |
|---|---|---|---|---|---|
| C1 | §9.6 取引数の十分性 | 独自の目安式 n ≈ (z_α / s)² | **MinTRL（Bailey & López de Prado 2012）** と PSR | v0.1 の式は MinTRL を粗く再発明したもので、歪度・尖度を正しく扱えていなかった | S-PSR |
| C2 | §10.2 Bootstrap のブロック長 | 「自己相関から自動選択 or 既定 5〜20 日」 | **Stationary Bootstrap ＋ Politis-White（2004）/ Patton ら（2009）の自動ブロック長**。arch の `StationaryBootstrap` と `optimal_block_length` を使う | 確立された手法と保守されている実装が存在する | S-SB, S-PW, S-ARCH |
| C3 | §10.1 White's RC | 将来機能として独立に実装 | **独立実装しない**。arch の `SPA`（RC を特殊ケースとして含む）から得る | arch の docstring が SPA を RC と同系統と明記。重複実装を避ける | S-ARCH, S-HANSEN |
| C4 | §10.1 SPA | Phase 2〜3 で SPA | **StepM（Romano-Wolf）を優先**し、SPA はラウンド単位の補助に | 本基盤が問うのは「どの候補が本物か」で、StepM が候補ごとに FWER 制御付きで答えられる | S-RW, S-ARCH |
| C5 | §10.1 PBO | 自前実装（方式は未定） | **R `pbo` 1.3.5 のアルゴリズムに忠実に移植**し、R `pbo` との数値一致を回帰テストにする。pypbo には依存しない。用途は**ラウンド単位の診断ゲート**と明記 | 実装は小さいので R への依存は過剰。pypbo は PyPI 未登録でテストが未整備 | S-PBO, S-PBO-R, S-PYPBO |
| C6 | §10.2 DSR の N_eff | 「試行間相関の階層クラスタ数等」（独自案） | **López de Prado & Lewis (2019) の手法**を採用（Phase 2） | 実効試行数の推定には原著の手法が存在する | S-ONC |
| C7 | §10.2 DSR の扱い（MVP） | MT5 のパス別 Sharpe で V[SR] を近似し、ゲートに使う | Telemetry Frames 導入までは **DSR を参考値（WARN）として扱い、FAIL 判定には使わない** | MT5 の Sharpe は計算定義が不明。DSR の式は年率化しない同一頻度の Sharpe を前提とする | S-DSR |
| C8 | §10.1 Haircut Sharpe | 将来機能 | **不採用**（DSR と重複。検算用に R quantstrat を任意で使う） | 同じ問いに 2 つの手法を持つと、閾値選びが恣意的になる | S-HL, S-QUANTSTRAT |
| C9 | §10.1 CPCV | 将来機能 | **不採用（ML 型 EA を導入するまで保留）**。導入時は skfolio を使う | 固定パラメータの評価では、組合せ数だけ MT5 で再最適化が必要になり、費用に見合わない。狙いは PBO と重なる | S-AFML, S-SKFOLIO-CPCV |
| C10 | §12.2–12.3 資金配分ルール | 独自の min(f_kelly_adj, f_DD, f_cap) | 同じ式を **Risk-Constrained Kelly（Busseti-Ryu-Boyd 2016）の 1 変数版**として位置付け直す。制約が「ピークからの DD」である点（原著は初期資産からの下落）を明記 | 単一 EA では、RCK の最適解が min(Kelly, 制約を満たす最大の f) と一致する。μ の推定誤差が支配的である（MTZ）ことが μ_LCB と分数化の根拠 | S-RCK, S-MTZ |
| C11 | §12.2 複数 EA の配分（将来） | multi_kelly（Σ⁻¹μ）＋全体スケール係数 | **多資産 RCK（cvxpy による凸最適化）** | Σ⁻¹μ は平均の推定誤差に極端に弱い。RCK は DD 制約を直接扱える | S-RCK, S-MTZ |
| C12 | §12.4 ロット換算 | ティック価値から自前で換算 | **MetaTrader5 API の `order_calc_profit` / `order_calc_margin` / `symbol_info`** を使う | 公式 API に既にある機能を再実装しない | S-MT5PY |
| C13 | §14.1 / §14.6 Telemetry | Frames を使い、単一テストも同様に出力する想定 | **単一テストでは Frames が使えない**（テスターイベントは最適化時のみ）→ 単一テストは `FILE_COMMON` にジョブ ID 付きのファイル名で出力。最適化は Frames を使い、`OnTesterDeinit` で遅延フレームも回収 | 公式 Book の仕様と、サンプルの実装パターン | S-MQL5BOOK-TESTER, S-MQL5BOOK-FRAME, S-MQL5BOOK-FILES |
| C14 | §14.4 開始検知 | 完了検知のみ | **開始検知を追加**（起動後一定時間内にテスターログへ開始行が出なければ `FAILED_TO_START`） | 「端末は起動するがテストが始まらない」という報告がある | S-FORUM-BATCH |
| C15 | §14.2 Optimization=3 | 言及なし | **使用禁止**と明記。XML の行数とパス数の照合を必須にする | build 6061 で XML が空になるという報告（未検証） | S-COMM-6061 |
| C16 | 付録 B / 実行環境 | Python バージョンは未指定 | **Python 3.12/3.13、実行ホストは Windows**。統計の中核は Linux CI でテスト可能。依存はロックファイルで固定（特に arch） | numpy/scipy が 3.12 以上を要求し、MetaTrader5 は Windows ホイールのみ。arch は v8.0.0 と main で SPA の挙動が違う | S-PYPI-VERS, S-MT5PY, S-ARCH |
| C17 | 開発ワークフロー | なし | リポジトリ直下に **`CLAUDE.md`** を追加し、調査ポリシー（一次情報優先・出典 ID の記録・既存実装の優先）を常時読み込まれる指示として残す | Claude Code の公式推奨（プロジェクト CLAUDE.md は VCS で共有、200 行未満、検証可能な指示） | S-CC-MEMORY |

### 変更しなかった主な判断（調査で妥当性を確認したもの）

- **OOS の封印・embargo**: AFML の purging/embargo の考え方と整合している [S-AFML]。
- **WFA を別トラックに分けたこと**: WFA は再最適化手続きの評価であり、固定パラメータ検証とは目的が違う [S-PARDO]。
- **ForwardMode=0 固定**: 期間管理は本基盤が持つ方針を維持する（MT5 のフォワード期間の結果を最適化時点で見ないため）。
- **パラメータ近傍ロバストネスを MVP の中心に置くこと**: 単純で、目的（孤立ピークの排除）に直結する。Sobol 等の大域感度分析は、必要になった時点で SALib を調査する。

## v0.3（2026-10-09）: P1 計画の不整合解消・実機確認結果の反映

対応表: D1〜D8 = `docs/plans/P1_PLAN.md` §9、E1 / F1〜F8 = `verification/p1/RECORD.md`。いずれもユーザー承認済み（E1: 2026-10-08、D・F: 2026-10-09）。

| # | 元 ID | 対象（ARCHITECTURE.md） | 変更前 | 変更後 | 理由 | 根拠 |
|---|---|---|---|---|---|---|
| C18 | D1 | §20 | P0 → P1（Telemetry は P2） | P0 のうち P1 に必要な最小限（ID・保存・分割定義の固定）と、Telemetry の**終了時出力**を P1 に前倒し | 「結果を正しく取得する」には Telemetry が必要 | P1_PLAN §9 |
| C19 | D2 | §5.2 environment、§8.1 run_id | env_id に端末ビルドを含め、run_id は period_id/env_id から作る | 「依頼したテスター設定」（ID に含める）と「観測した環境」（job に記録）を分ける。P1 の run_id は日付＋設定ハッシュから作る | ビルドは実行後にしか分からない。P1 には Study・期間テーブルがない | P1_PLAN §9 |
| C20 | D3 | §8.1 | strategy_version_id = hash(ex5 + mq5 + telemetry) | mq5 は任意（ソース非公開 EA は null、`source_available=false`） | ソース非公開の EA を扱えない | P1_PLAN §9 |
| C21 | D4 | §8.1 | 全パラメータを candidate_id に含める | `RL_` で始まるハーネス用の入力は除外 | job_id を含めると実行ごとに ID が変わる | P1_PLAN §9 |
| C22 | D5 | §14.4 | 完了条件に「レポートが解析可能」 | 単一テストではレポートを解析しない（存在すれば保存のみ） | 正本は Telemetry。レポートは UI 言語で出力される | P1_PLAN §9, S-HW-P1 |
| C23 | D6 | §14.1 | Python API とテスター起動を同じ端末で併用する想定 | P1 では Python API を使わない。将来の併用は別インスタンスか実行順序を決める | 同じポータブル端末の取り合い | P1_PLAN §9 |
| C24 | D7 | §7.2 | Guard 拒否は「例外で拒否」 | 「正常な判定結果としての拒否」（監査ログ＋終了コード 3）に統一 | §15.1 との表現の不一致 | P1_PLAN §9 |
| C25 | D8 | §14.5 | 価格履歴のチェックサムを Python API で取得 | P1 では取らず、テレメトリが観測した最初・最後のティック時刻・ティック数・バー数を簡易の指紋とする | P1 では Python API を使わない | P1_PLAN §9 |
| C26 | E1 | §19.2 | 約定の Parquet ハッシュで再現性を比較 | 正規化 CSV の内容ハッシュで比較。Parquet のバイト列は比較に使わない | Parquet はライブラリのバージョン・圧縮設定でバイト列が変わる（実測） | S-PYPI-VERS2 |
| C27 | F1 | §14.2 | `.set` の文字コード・書式は未確定 | UTF-16LE＋BOM・CRLF。数値等は `値\|\|値\|\|刻み\|\|値\|\|N`、文字列は `name=value`、datetime は Unix 秒 | UTF-8 は ANSI として読まれ非 ASCII が化けた | S-HW-P1 |
| C28 | F2 | §7.2、§8.1、§14.2 | 期間の包含関係は未確定 | 期間は半開区間 `[from, to)`（サーバ時刻）で記録。Guard は閉区間で保守的に判定。**履歴が依頼期間をカバーしない場合は `QUARANTINED`（`DATA_COVERAGE`、許容日数 既定 7）** | ToDate は含まれなかった。デモサーバの履歴は 2021-12-22 から | S-HW-P1 |
| C29 | F3 | §14.4 | 開始検知はテスターログ、完了はプロセス終了＋マーカー | 開始・成功は**端末ログ**の `automatic testing started` / `last test passed with result "successfully finished"`。起動前のファイルサイズ以降だけを読む | ログは日付ごとの追記型、本文は英語 | S-HW-P1 |
| C30 | F4 | §14.6 | 約定の Σ ≒ 純損益 | 売買約定の件数 = `STAT_DEALS` かつ Σ(profit+commission+swap+fee) = `STAT_PROFIT`。magic で絞り込まない | 実機で両等式が成立。強制決済は magic=0 | S-HW-P1 |
| C31 | F5 | §5.2、§14.2 | 銘柄仕様に spread を含める | spread は観測値として別記録し、照合ハッシュに含めない。時刻はすべてサーバ時刻と明記 | spread は実行ごとに変動。`TimeGMT()` はテスター内でサーバ時刻 | S-HW-P1 |
| C32 | F6 | §14.5、§19.2 | ビルド不一致は警告のみ | 加えて、ビルドの異なるジョブ同士の再現性比較は「比較不能」として扱う | 実行中に新ビルドの自動ダウンロードを観測 | S-HW-P1 |
| C33 | F7 | P1_PLAN §4 | タイムアウトの既定値は未定 | 全体 3600 秒、開始検知 300 秒 | 初回はティック履歴のダウンロードに時間がかかる（1 週間分でも約 3 分） | S-HW-P1 |
| C34 | F8 | P1_PLAN §5 | `workspace/` を git 管理外にする | 加えて、ログ artifacts に口座番号と接続元 IP が含まれることを明記し、CLI はログ本文を表示しない | 端末ログに口座番号と IP を観測 | S-HW-P1 |
