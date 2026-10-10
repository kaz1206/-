# 情報源レジストリ（Source Registry）

重要な設計判断は、本ファイルの ID（例: `[S-DSR]`）で根拠を参照する。
調査日: 2026-10-08

## 確認レベル

| レベル | 意味 |
|---|---|
| **A** | 一次情報を直接読んで確認（公式パッケージ実物・ライブラリのソースコード・公式ドキュメント本文） |
| **B** | 一次情報は特定済み。内容は検索エンジン経由の要約で確認（全文は未読） |
| **C** | 二次情報のみ（フォーラム・コミュニティ・第三者ドキュメント） |
| **V** | Windows/MT5 実機での確認が実装前に必須（Web 上に完全な情報なし） |

> 注意: 本調査環境ではネットワークポリシーにより `mql5.com`, `metatrader5.com`, `arch.readthedocs.io`, `ssrn.com`, `escholarship.org`, `wikipedia.org` への直接アクセスがブロックされていた。
> そのため、これらのサイトの内容は B/C レベルに留まる。実装前に原ページで再確認すること（`FINDINGS.md` §3 のチェックリスト）。

---

## 1. 公式ドキュメント・公式実装

| ID | 情報源 | 確認した内容 | レベル |
|---|---|---|---|
| S-MT5PY | PyPI `MetaTrader5` 5.0.6231（2026-09-27）パッケージ実物（`_core.cp312-win_amd64.pyd` と `__init__.py`） | 公開関数一覧（`initialize`, `login`, `shutdown`, `version`, `last_error`, `terminal_info`, `account_info`, `symbol_info`, `symbol_info_tick`, `symbols_get`, `symbol_select`, `copy_rates_*`, `copy_ticks_*`, `orders_*`, `positions_*`, `history_orders_*`, `history_deals_*`, `order_calc_margin`, `order_calc_profit`, `order_check`, `order_send`, `market_book_*`）。**Strategy Tester 関連の関数は存在しない**。配布ホイールは **win_amd64 のみ**（cp36〜cp314） | **A** |
| S-MT5PYDOC | https://www.mql5.com/en/docs/integration/python_metatrader5 （PyPI から参照される公式リファレンス） | 各関数の詳細シグネチャ | B（ページはブロック） |
| S-MT5START | MetaTrader 5 ヘルプ「Platform Start – For Advanced Users」 https://www.metatrader5.com/en/terminal/help/start_advanced/start | `/config:` による起動。`[Tester]` セクションの `Expert`（ないとテストが始まらない）, `Symbol`, `Period`（省略時 H1）, `ShutdownTerminal`（省略時 0）等 | B |
| S-MT5B4230 | MetaTrader 5 build 4230 リリースノート https://www.metaquotes.net/en/metatrader5/news/5441 | `[StartUp]` に `ShutdownTerminal` が追加された（スクリプト専用）。`MQL_STARTED_FROM_CONFIG` も追加。既存の `[Tester]` の `ShutdownTerminal` とは別物 | B |
| S-MT5ART20147 | MQL5 記事「Automating Terminal Startup for Service Tasks」 https://www.mql5.com/en/articles/20147 | `[StartUp]` でスクリプトを実行 → 終了し、終了コードを返す | B |
| S-MT5B430 | MT5 build 430 リリースノート https://www.metatrader5.com/en/releasenotes/terminal/430 | configuration.ini に `ForwardMode`/`ForwardDate` が追加された（古いビルドなので、現行の値は要確認） | B |
| S-MT5REPORT | MQL5 記事 5436 https://www.mql5.com/en/articles/5436 | 単一テストのレポート = **HTML/XLSX**、最適化レポート = **XML** | B |
| S-MQL5BOOK-TESTER | MQL5 Book「OnTesterInit/OnTesterPass/OnTesterDeinit」 https://www.mql5.com/en/book/automation/tester/tester_ontester_init_pass_deinit | 3 つのイベントは**最適化時のみ発火**（単一テストでは発火しない）。取引しない端末側の別インスタンスで動く。`MQL_FRAME_MODE` で判定 | B |
| S-MQL5BOOK-FRAME | MQL5Book のサンプル `FrameTransfer.mq5`（forge.mql5.io） | `OnTesterPass` でフレームを処理し、**`OnTesterDeinit` で最後に `FrameNext` ループを回して遅れて届いたフレームを回収**する。受信したフレームは `.mqd` ファイルに保存される | B/C |
| S-MQL5BOOK-FILES | MQL5 Book「Information storage methods」 https://www.mql5.com/en/book/common/files | テスター内で書いたファイルは `Tester/Agent-*/MQL5/Files`（エージェントのサンドボックス）に保存される。`FILE_COMMON` を付けると `Terminal/Common/Files` | B |
| S-MT5CUSTOM | MQL5 記事 3540 https://www.mql5.com/en/articles/3540 | カスタムシンボルは Strategy Tester で使える。同じ名前でも端末ごとに履歴が違いうるため、Cloud Network では使えない | B |
| S-MT5OPTTYPES | MetaTrader 5 ヘルプ「Optimization Types」 https://www.metatrader5.com/en/terminal/help/algotrading/optimization_types （2026-10-10、直接取得できず検索要約で確認） | 最適化の種類: 低速な完全アルゴリズム（全組合せ）、高速な遺伝的アルゴリズム、気配値表示の全銘柄。最適化キャッシュは `Tester/cache` の `.opt` | B |
| S-MT5B619 | MetaTrader 5 build 619 リリースノート https://www.metatrader5.com/en/releasenotes/terminal/619 （検索要約） | `FrameAdd` / `FrameFirst` / `FrameNext` / `FrameInputs` の導入。フレームは `OnTester` から送り、到着で `OnTesterPass` が発生する | B |
| S-FORUM-OPTXML | MQL5 フォーラム https://www.mql5.com/en/forum/515664 、https://www.mql5.com/en/forum/393733/48983671 、https://www.mql5.com/en/forum/2805/38982 | 最適化 XML の列（Pass、Result, Profit, Gross Profit, …）。`Result` は最適化基準の値。UI でパスが欠けて見えても XML には全行がある、という報告 | C |
| S-ARCH | `arch` 8.0.0（PyPI, 2025-10-21）。ソース: https://github.com/bashtage/arch のタグ `v8.0.0` | `SPA`, `StepM`, `MCS`（入力は**損失（loss）**の T×k 配列）、`StationaryBootstrap`/`CircularBlockBootstrap`/`MovingBlockBootstrap`、`optimal_block_length`（Politis-White 2004 ＋ Patton-Politis-White 2009 の補正。戻り値は `b_sb`, `b_cb`）。**main ブランチでは v8.0.0 以降に「ブートストラップ内での studentize」が追加されており、リリース版と挙動が異なる** | **A** |
| S-PBO-R | CRAN `pbo` 1.3.5（MIT, Matt Barry） https://github.com/cran/pbo | CSCV の参照実装: T×N 行列、S は T を割り切る、`combn(S, S/2)`、IS の最良 → OOS での順位 → logit | **A**（ソース確認） |
| S-PYPBO | `esvhd/pypbo` の README | PBO/PSR/DSR/MinTRL の Python 実装。**PyPI 未登録。README に「Add test cases」が TODO と記載** | A（README） |
| S-SKFOLIO-CPCV | skfolio `CombinatorialPurgedCV` https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html | 既存の CPCV 実装 | C（検索経由） |
| S-PA-R | R PerformanceAnalytics の `ProbSharpeRatio`, `MinTrackRecord` | PSR/MinTRL の既存実装（テストの検算に使える） | C |
| S-QUANTSTRAT | R quantstrat の `haircutSharpe` / `profitHurdle` | Harvey-Liu の既存実装 | C |
| S-CC-MEMORY | Claude Code Docs「How Claude remembers your project」 https://code.claude.com/docs/en/memory | プロジェクトの `CLAUDE.md` は VCS で共有する。**1 ファイル 200 行未満が目安**。検証可能な具体的指示で書く。`@path` インポート（コンテキスト量は減らない）。`.claude/rules/` は `paths` フロントマター付き。CLAUDE.md はあくまで文脈であり強制力のある設定ではない（強制したい場合は hooks） | **A** |
| S-PYPI-VERS | PyPI JSON API（2026-10-08 時点） | 各ライブラリの最新版（`FINDINGS.md` §4） | **A** |
| S-PYPI-VERS2 | PyPI JSON API（2026-10-08、P1 実機確認フェーズで追加確認） | PyYAML 6.0.3（2025-09-25, ≥3.8）、uv 0.12.23（2026-10-03）、pyarrow 25.0.1 の Parquet は `created_by` にライブラリのバージョンを埋め込む（本環境で実測） | **A** |

| S-HW-P1 | 実機確認（2026-10-09）: Windows 11、MT5 build 6230（ポータブル）、MetaQuotes-Demo、検証 EA `RL_Verify_P1`。記録は `verification/p1/RECORD.md` | `/config` 起動、`.set` 形式、Model 対応、Report/ログの場所と文字コード、`FILE_COMMON`、`OnDeinit` での約定・統計の取得、期間の包含関係、再実行の挙動 | **A**（実機観測） |

| S-HW-P1-E2E | 実機 E2E（2026-10-10）: P1 本体の `rlab backtest run` を `C:\MT5_verify` で実行。端末ログ | 相対パスの `/config` は `cannot load config ... at start` で失敗する。起動時に保留中のアップデートがあると、LiveUpdate が `/update ... /config:<ini>` を引き継いで端末を終了し、約 15 秒後に更新後の端末（6230 → 6251）が同じ ini で再起動してテストを完了する | **A**（実機観測） |

| S-HW-P2 | 実機確認（2026-10-10）: Telemetry v2、`RL_SmokeTest` EURUSD H1 2023 年、build 6251。`rlab metrics show` の照合表と約定一覧（`tests/fixtures/hw_20261010`） | `STAT_MAX_CONWINS` 等の意味（金額と回数）、最長の連続が複数あるときの MT5 の選び方、エクイティ DD の差（0.26%）、Telemetry v2 の所要時間（+16%）、MT5 シャープ ≈ 日次シャープ × √(日数) | **A**（実機観測） |

## 2. 原著論文・学術資料

| ID | 書誌情報 | 用途 | レベル |
|---|---|---|---|
| S-PSR | Bailey, D. H., & López de Prado, M. (2012). The Sharpe ratio efficient frontier. *Journal of Risk*, 15(2). | PSR・MinTRL（歪度・尖度による非正規性の補正と、必要な標本長） | B |
| S-DSR | Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for selection bias, backtest overfitting and non-normality. *JPM*, 40(5), 94–107. doi:10.3905/jpm.2014.40.5.094 / SSRN 2460551 | DSR = 期待最大 Sharpe SR₀（N と V[SR] の関数）を基準値にした PSR | B |
| S-PBO | Bailey, D. H., Borwein, J., López de Prado, M., & Zhu, Q. J. (2017). The probability of backtest overfitting. *Journal of Computational Finance*, 20(4). SSRN 2326253 | CSCV による PBO。性能劣化・損失確率・確率優位も算出 | B |
| S-ONC | López de Prado, M., & Lewis, M. J. (2019). Detection of false investment strategies using unsupervised learning methods. *Quantitative Finance*, 19(9), 1555–1565. SSRN 3167017 | クラスタリングで**実効試行数**を推定 | B |
| S-WHITE | White, H. (2000). A reality check for data snooping. *Econometrica*, 68(5), 1097–1126. doi:10.1111/1468-0262.00152 | Reality Check | B |
| S-HANSEN | Hansen, P. R. (2005). A test for superior predictive ability. *JBES*, 23(4), 365–380. | SPA（studentize、劣後モデルを除いた再中心化） | B |
| S-RW | Romano, J. P., & Wolf, M. (2005). Stepwise multiple testing as formalized data snooping. *Econometrica*, 73(4), 1237–1282. | StepM（FWER を制御しつつ優越モデルを個別に特定） | A（arch の docstring で引用を確認） |
| S-HLN | Hansen, P. R., Lunde, A., & Nason, J. M. (2011). The model confidence set. *Econometrica*, 79(2). | MCS | A（arch の docstring で引用を確認） |
| S-SB | Politis, D. N., & Romano, J. P. (1994). The stationary bootstrap. *JASA*, 89(428). | 定常ブートストラップ | A（arch で引用を確認） |
| S-PW | Politis, D. N., & White, H. (2004). Automatic block-length selection for the dependent bootstrap. *Econometric Reviews*, 23(1). 補正: Patton, Politis & White (2009) | ブロック長の自動選択 | A（arch の docstring で引用を確認） |
| S-HL | Harvey, C. R., & Liu, Y. (2015). Backtesting. *JPM*, 42(1), 13–28. https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF | Haircut Sharpe、利益ハードル | B |
| S-AFML | López de Prado, M. (2018). *Advances in Financial Machine Learning*, Wiley, 第 7 章（Purged K-Fold, CPCV）、第 11–12 章 | CPCV、purging/embargo | B |
| S-PARDO | Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies* (2nd ed.). Wiley. | Walk-Forward Analysis、Walk-Forward Efficiency（WFE） | C（閾値は未確認） |
| S-KELLY | Kelly, J. L. (1956). A new interpretation of information rate. *Bell System Technical Journal*, 35(4). | Kelly 基準 | 古典（今回の調査では未読） |
| S-MTZ | MacLean, L. C., Thorp, E. O., & Ziemba, W. T. (2010). Long-term capital growth: the good and bad properties of the Kelly and fractional Kelly capital growth criteria. *Quantitative Finance*, 10(7). / "Good and Bad Properties of the Kelly Criterion"（Berkeley Stat157 資料） | **平均の推定誤差の影響が支配的**（平均:分散:共分散 ≈ 20:2:1、対数効用では最大 約 100:3:1）。過大ベットの危険。Fractional Kelly を使う根拠 | B |
| S-RCK | Busseti, E., Ryu, E. K., & Boyd, S. (2016). Risk-constrained Kelly gambling. *Journal of Investing*, 25(3), 118–134. arXiv:1603.06183 | 一定水準まで資産が落ち込む確率に制約を置いて成長率を最大化。凸近似で解く。**同じ DD リスクで Fractional Kelly を上回る**と報告 | B |
| S-LW | Ledoit, O., & Wolf, M. (2004). A well-conditioned estimator for large-dimensional covariance matrices. *JMVA*, 88(2). | 共分散の縮小推定（将来） | 古典（今回の調査では未読） |

## 3. 補助資料（コミュニティ等）

| ID | 情報源 | 用途 | レベル |
|---|---|---|---|
| S-FORUM-BATCH | MQL5 フォーラム「Strategy Tester Batch Mode」 https://www.mql5.com/en/forum/361861 | `[Tester]` 設定で端末は起動するがテストが実行されない、という報告（既知の落とし穴） | C |
| S-COMM-6061 | コミュニティのスキル文書（claudemarket.ai「mt5-robot-tester」） | **build 6061 で `Optimization=3` の XML が空になる**という報告 | C（未検証） |
| S-FORUM-LATEFRAME | MQL5 ドイツ語フォーラムのスレッド（FrameNext と遅延フレーム） | `OnTesterPass` の後に届くフレームの扱い | C |
| S-PBO-CRIT | arXiv:1905.05023（Covariance-Penalties）ほか | 「PBO は時系列依存を保存する」という主張には異論がある | C |
