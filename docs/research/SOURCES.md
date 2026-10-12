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
| S-MQL5-CUSTOM | MQL5 リファレンス「カスタム銘柄」（`CustomSymbolCreate`・`CustomRatesReplace`・`CustomSymbolSetInteger`・`SymbolExist`）と、Strategy Tester がカスタム銘柄でテストできること | P10 の忠実度チェック（合成 H0 価格の銘柄を作る） | B（記憶による。公式ページはブロック。実機で確認） |
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
| S-ARCH-800 | arch 8.0.0 を PyPI から実際にインストールして確認（2026-10-11、確認後は依存から外した） | 最新版は 8.0.0（2025-10-21）。依存に pandas・scipy・statsmodels。`StationaryBootstrap(block_size, data, seed=Generator)`、`conf_int(func, reps, method∈{basic, percentile, studentized, norm, bc, bca}, size, tail)`。`optimal_block_length` の戻り値の列名は**実物では `stationary` / `circular`**（説明文の `b_sb` / `b_cb` と違う）。Generator のシードで結果が再現する。cp312/cp313 の win_amd64 ホイールあり | **A**（実物） |
| S-PYPI-PARETO | PyPI の JSON API（2026-10-11 確認）: pymoo 0.6.2（2026-06-28 公開。numpy・scipy・moocore・autograd・cma・matplotlib などに依存）、paretoset 1.2.5（2025-05-25 公開。numba・numpy・pandas に依存） | パレートの層を既存ライブラリで計算するかの判断（C68）。候補数が数百以下なので O(n²) の自前比較を選んだ | **A**（PyPI で確認） |
| S-PBO-R | CRAN `pbo` 1.3.5（MIT, Matt Barry） https://github.com/cran/pbo | CSCV の参照実装: T×N 行列、S は T を割り切る、`combn(S, S/2)`、IS の最良 → OOS での順位 → logit | **A**（ソース確認） |
| S-PYPBO | `esvhd/pypbo` の README | PBO/PSR/DSR/MinTRL の Python 実装。**PyPI 未登録。README に「Add test cases」が TODO と記載** | A（README） |
| S-SKFOLIO-CPCV | skfolio `CombinatorialPurgedCV` https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html | 既存の CPCV 実装 | C（検索経由） |
| S-PA-R | R PerformanceAnalytics 2.1.0（CRAN、2026-04-11）`R/ProbSharpeRatio.R`・`R/MinTRL.R`（CRAN の GitHub ミラー https://github.com/cran/PerformanceAnalytics で 2026-10-11 に確認） | PSR = pnorm((sr − refSR)·√(n−1) / √(1 − sr·sk + sr²·(kr−1)/4))、MinTRL = 1 + (1 − sk·sr + (kr−1)/4·sr²)·(qnorm(p)/(sr − refSR))²。sr は `SharpeRatio(..., "StdDev")`、kr は `kurtosis(method="moment")`（超過でない）。**既定値は `ignore_kurtosis = TRUE`（kr = 3 に固定）**。例: `sr = 2/√12, refSR = 1/√12, sk = −0.72, kr = 5.78, n = 59` | **A**（ソース確認） |
| S-SCIPY-NDIMAGE | SciPy 1.18.1（`uv.lock` で固定）の `scipy.ndimage.generic_filter` / `label` / `generate_binary_structure` の docstring と実際の動作（2026-10-11、docs.scipy.org は本環境から接続不可のため固定版の実物で確認） | `generic_filter` の既定 `mode='reflect'` は端の外を鏡写しで埋める（`mode='constant', cval=NaN` で外を NaN にできる）。`label` の既定の連結は 1（2 次元で 4 近傍）、`generate_binary_structure(d, d)` で斜めを含む連結。`percentile_filter` は NaN を無視しない | **A**（固定版の実物） |
| S-NUMPY-PCT | NumPy（`uv.lock` で固定した版）の `nanpercentile` の動作確認（2026-10-11） | `method='linear'`（既定）は −∞ を含むと NaN を返す（警告 invalid value）。`inverted_cdf` と `lower` は −∞ を返す | **A**（実物の動作） |
| S-QUANTSTRAT | R quantstrat `R/deflated.Sharpe.R`（GitHub braverock/quantstrat の master、2026-10-11 に確認）。Harvey-Liu の `haircutSharpe` / `profitHurdle` も同パッケージ | `.deflatedSharpe`: maxZ = (1−γ)·qnorm(1−1/N) + γ·qnorm(1−1/(N·e))、SR₀ = √(V/periodsInYear)·maxZ、PSR(SR₀) の式は原著と同じ。**ただし V は「純損益の変化率」の分散（原著は試行間の Sharpe の分散）、出力の deflated.Sharpe は Sharpe × PSR(SR₀) という独自の値** | **A**（ソース確認） |
| S-CC-MEMORY | Claude Code Docs「How Claude remembers your project」 https://code.claude.com/docs/en/memory | プロジェクトの `CLAUDE.md` は VCS で共有する。**1 ファイル 200 行未満が目安**。検証可能な具体的指示で書く。`@path` インポート（コンテキスト量は減らない）。`.claude/rules/` は `paths` フロントマター付き。CLAUDE.md はあくまで文脈であり強制力のある設定ではない（強制したい場合は hooks） | **A** |
| S-PYPI-VERS | PyPI JSON API（2026-10-08 時点） | 各ライブラリの最新版（`FINDINGS.md` §4） | **A** |
| S-PYPI-VERS2 | PyPI JSON API（2026-10-08、P1 実機確認フェーズで追加確認） | PyYAML 6.0.3（2025-09-25, ≥3.8）、uv 0.12.23（2026-10-03）、pyarrow 25.0.1 の Parquet は `created_by` にライブラリのバージョンを埋め込む（本環境で実測） | **A** |

| S-HW-P1 | 実機確認（2026-10-09）: Windows 11、MT5 build 6230（ポータブル）、MetaQuotes-Demo、検証 EA `RL_Verify_P1`。記録は `verification/p1/RECORD.md` | `/config` 起動、`.set` 形式、Model 対応、Report/ログの場所と文字コード、`FILE_COMMON`、`OnDeinit` での約定・統計の取得、期間の包含関係、再実行の挙動 | **A**（実機観測） |

| S-HW-P1-E2E | 実機 E2E（2026-10-10）: P1 本体の `rlab backtest run` を `C:\MT5_verify` で実行。端末ログ | 相対パスの `/config` は `cannot load config ... at start` で失敗する。起動時に保留中のアップデートがあると、LiveUpdate が `/update ... /config:<ini>` を引き継いで端末を終了し、約 15 秒後に更新後の端末（6230 → 6251）が同じ ini で再起動してテストを完了する | **A**（実機観測） |

| S-HW-P2 | 実機確認（2026-10-10）: Telemetry v2、`RL_SmokeTest` EURUSD H1 2023 年、build 6251。`rlab metrics show` の照合表と約定一覧（`tests/fixtures/hw_20261010`） | `STAT_MAX_CONWINS` 等の意味（金額と回数）、最長の連続が複数あるときの MT5 の選び方、エクイティ DD の差（0.26%）、Telemetry v2 の所要時間（+16%）、MT5 シャープ ≈ 日次シャープ × √(日数) | **A**（実機観測） |
| S-HW-P3 | 実機確認（2026-10-10）: `verification/p3`、`RL_Verify_P3`、EURUSD H1 2023-01〜03、M1 OHLC、12 パス、build 6251。記録は `verification/p3/RECORD.md` | `Optimization=1` の ini 起動、テスターログの最適化完了行とエージェント内訳、XML の形式（UTF-8、英語の列名、Result 降順、最適化した入力だけ）、Frames の到着（複数/回・順不同）と 161 KB のフレーム、キャッシュによる未実行、ローカルエージェントだけの使用、単一テストとの一致 | **A**（実機観測） |
| S-HW-P3E2E | 実機 E2E（2026-10-10）: `rlab optimize run configs/studies/rl_smoketest_grid.yaml`（1,044 パス、Real ticks、EURUSD H1 2022-01-03〜2024-01-01、build 6251、2 ジョブ）と単一テスト `x8_check_eurusd_h1.yaml` | 全パス取込（580/580、464/464）、ローカルエージェントのみ、単一テストと最適化パス 127 の完全一致（Real ticks）、最長連敗が同じ長さで 2 つあるときの MT5 の選び方（金額の絶対値が最大） | **A**（実機観測） |
| S-HW-P6 | 実データでの動作確認（2026-10-11、Windows、Python 3.13、arch 8.0.0）: `rlab stats run or_9795248161825c3e --filter fl_d5c3a7c788f7c135 --candidate cd_b94d50a54395ef54`（`st_faa690e2f71ce49c`、再実行で ALREADY_DONE、`--seed 1` で `st_848fe85f3aad15e9`）、`rlab stats show`、Windows の `pytest`（206 passed, 52 skipped） | 全 1,044 候補の DSR 保存、ファミリーの N = 試行台帳の合計、取引なし 55 件を V から除外、冪等性とシードによる別実行、DSR はシードに依存せずブートストラップだけ変わる、P6 の検証テストが Windows でも合格 | **A**（実機観測） |
| S-HW-P7 | 実データでの動作確認（2026-10-11、Windows、Python 3.13）: `rlab select run or_9795248161825c3e --filter fl_d5c3a7c788f7c135 --robust rb_f963c7958c695702 --stats st_faa690e2f71ce49c`（`sl_4ea20525fe6af425`、再実行で ALREADY_DONE）、Windows の `pytest`（229 passed, 57 skipped） | P4 合格 0 件での `NO_ELIGIBLE` の正常終了、既知の限界の表示、冪等性、P7 のテストが Windows でも合格 | **A**（実機観測） |
| S-HW-P8A | 実機確認（2026-10-11、Windows、診断モード）: `rlab validate lock sl_4ea20525fe6af425 --plan configs/validation_plans/rl_smoketest_v1.yaml --diagnostic --candidate cd_b94d50a54395ef54`（`vp_a080da87b2f754a7`）、`rlab validate run` | V0（2020-01-01〜2021-12-24）・V1（2024-01-11〜2024-06-20）の Real ticks の単一テストがこのブローカーで成功（データ範囲の問題なし）、Discovery の再現（純損益 63.03・取引 307 が最適化パスと一致）、追加コストの換算（tick_size 0.00001、tick_value 1.0）、テスト終了時のスプレッド観測値 15・37 ポイント | **A**（実機観測） |
| S-HW-P8B | 練習用ワークスペースでの実機確認（2026-10-11、Windows）: `workspace_rehearsal`、`configs/rehearsal/`（Holdout 2019-01-02〜2019-06-28）、`rlab optimize run` → `filter` → `robust` → `stats` → `select` → `validate`（診断）→ `oos lock --rehearsal` → `oos run`（フラグなし／あり／2 回目）→ `oos lock`（2 回目） | 2018〜2019 年の Real ticks の履歴がこのブローカーにある、確認フラグなしでは何もしない、実行前の燃焼、2 回目の実行と別マニフェストのロックの拒否、事前計算した ID の一致、Discovery の再現 | **A**（実機観測） |
| S-HW-P9 | 実機確認（2026-10-12、Windows・デモ口座、MetaTrader5 5.0.6231） | `account_info()` の `currency` `leverage` `margin_so_mode` `margin_so_call` `margin_so_so` `equity` `margin_mode`、`symbol_info()` の `volume_min` `volume_step` `volume_max` `trade_contract_size` `currency_profit` `currency_margin` `digits` `point` `trade_calc_mode`（EURUSD = 0）が存在。`order_calc_margin(ORDER_TYPE_BUY/SELL, sym, 1.0, price)`・`order_calc_profit(ORDER_TYPE_BUY, sym, 1.0, p0, p1)` は口座通貨で返る（JPY 口座で 177,384 円・1,583 円）。週末は bid = ask | V |
| S-HW-P10 | 実機確認（2026-10-12、Windows、build 6256） | `CustomSymbolCreate`・`CustomRatesReplace`（M1 足 374,400 本、戻り値 374,400）がスクリプトから動く。カスタム銘柄でも Strategy Tester は開始前に約 100 本の履歴を必要とし、ないと開始を後ろにずらす（H4・開始 2012-01-02 → 最初のティック 2012-01-25）。`CustomSymbolCreate(名前, パス, 元の銘柄)` は基軸通貨・損益通貨をコピーせず名前の文字から作り（`RL_` / `H0_`）、ティックサイズ・ティック価値は 0（`OrderCalcProfit` がエラー 4758）。`CustomSymbolSetString` で通貨を設定すると true が返るが値は変わらない（FOREX 方式では名前の先頭 6 文字から決まる。`EURUSD_RLH0` で基軸 EUR・損益 USD になることを確認）。カスタム銘柄では JPY 口座の端末上の `OrderCalcProfit` が失敗したまま（換算経路）だが、USD 口座のテスターでは損益が正しく計算された。1 minute OHLC のティックの時刻は分の頭から +30 秒。テスター終了時に建玉を自動決済（コメント `end of test`） | V |

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
| S-BGK | Broadie, M., Glasserman, P., & Kou, S. (1997). A continuity correction for discrete barrier options. *Mathematical Finance*, 7(4), 325–349. | 日次（離散）監視の到達確率を連続のブラウン運動の閉形式と比べるとき、障壁を 0.5826σ√Δt ずらす補正。P9 事前シミュレーション R1b/R1c の基準値に使用（記憶による。式は R1c で数値的に整合を確認） | B |
| S-CP | Clopper, C. J., & Pearson, E. S. (1934). The use of confidence or fiducial limits illustrated in the case of the binomial. *Biometrika*, 26(4), 404–413. | 二項比率の正確な信頼限界（ベータ分布の分位点）。P9 で破産・DD 超過の割合をモンテカルロ誤差込みで安全側に判定する（`scipy.stats.beta.ppf`） | B |
| S-LW | Ledoit, O., & Wolf, M. (2004). A well-conditioned estimator for large-dimensional covariance matrices. *JMVA*, 88(2). | 共分散の縮小推定（将来） | 古典（今回の調査では未読） |
| S-MOP2012 | Moskowitz, T. J., Ooi, Y. H., & Pedersen, L. H. (2012). Time series momentum. *Journal of Financial Economics*, 104(2), 228–250. doi:10.1016/j.jfineco.2011.11.003（SSRN 2089463、AQR の論文ページとデータページ） | 58 の先物・フォワード（株価指数・通貨・商品・債券）で、過去 12 か月の超過リターンが将来のリターンを予測する。持続は 1〜12 か月で、それより長いと一部反転する。AQR の公開データは「振り返り 12 か月・保有 1 か月」。分散したポートフォリオの成績が主張の中心。**1 銘柄ごとの Sharpe の値は本文で確認していない** | B（要旨・AQR のページで確認。本文は未読） |
| S-HOP2017 | Hurst, B., Ooi, Y. H., & Pedersen, L. H. (2017). A Century of Evidence on Trend-Following Investing. *Journal of Portfolio Management*, 44(1)（2012 年版は 1903 年から） | 振り返り 1・3・12 か月の 3 つの時系列モメンタムを組み合わせ、67 市場で検証。年率 Sharpe 約 0.4（手数料控除後）などの数字は二次資料による | C（二次資料。本文の数値は未確認） |

## 3. 補助資料（コミュニティ等）

| ID | 情報源 | 用途 | レベル |
|---|---|---|---|
| S-FORUM-BATCH | MQL5 フォーラム「Strategy Tester Batch Mode」 https://www.mql5.com/en/forum/361861 | `[Tester]` 設定で端末は起動するがテストが実行されない、という報告（既知の落とし穴） | C |
| S-COMM-6061 | コミュニティのスキル文書（claudemarket.ai「mt5-robot-tester」） | **build 6061 で `Optimization=3` の XML が空になる**という報告 | C（未検証） |
| S-FORUM-LATEFRAME | MQL5 ドイツ語フォーラムのスレッド（FrameNext と遅延フレーム） | `OnTesterPass` の後に届くフレームの扱い | C |
| S-PBO-CRIT | arXiv:1905.05023（Covariance-Penalties）ほか | 「PBO は時系列依存を保存する」という主張には異論がある | C |
