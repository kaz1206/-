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
