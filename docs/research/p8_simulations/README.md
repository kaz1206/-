# P8 計画の根拠にした試算（2026-10-11）

`docs/plans/P8_PLAN.md` §4.1 の数字の出どころ。検証 630 日、各 2,000 回、シード 20261011。

    uv run python docs/research/p8_simulations/validation_gate.py 2000          # 全 16 状況を順に
    uv run python docs/research/p8_simulations/validation_gate.py 2000 5 6      # 状況の番号を指定（並列実行用）

出力（2026-10-11。作業用プロセスの再起動で途中から番号指定で再実行した）:

    K=1 iid                    null 0.0400 (0.0318, 0.0495)
    K=1 ar1_0.4                null 0.0445 (0.0359, 0.0545)
    K=1 ar2                    null 0.0650 (0.0546, 0.0767)
    K=1 ma5                    null 0.0140 (0.0093, 0.0202)
    K=1 garch_ar1              null 0.0430 (0.0345, 0.0528)
    K=5 iid corr 0.5           null 0.0325 (0.0252, 0.0412)
    K=5 ar2 corr 0.5           null 0.0810 (0.0694, 0.0938)
    K=5 iid edge 0.05/.08/.12  power 0.1300 / 0.3550 / 0.7245 (null 0.033-0.035)
    K=5 ar1_0.2 edge           power 0.1010 / 0.2540 / 0.5490 (null 0.0365-0.039)
    K=1 iid edge               power 0.3330 / 0.6145 / 0.8960

本体（`src/`）からは参照しない。実装では本番の関数で同じ実験を合格基準つきのテストとして書き直す。

## 第 3 版の検証（H1〜H4、`validation_gate2.py`、2026-10-11）

V0（516 日）と V1（115 日）を別々に生成し、P8_PLAN §4.2.1 の期間別の集約で p 値を計算した。基準はコミット 82ebd94 で実行前に固定。結果は P8_PLAN §4.4。第 2 版の `validation_gate.py`（1 本につないだ系列）は参考として残し、判定には使わない。

    uv run python docs/research/p8_simulations/validation_gate2.py h1:iid h1:ar1_0.4 h1:ma5 h1:garch_ar1 h1:vol2 h1:ar2
    uv run python docs/research/p8_simulations/validation_gate2.py h2
    uv run python docs/research/p8_simulations/validation_gate2.py h3:iid:0.5 h3:iid:0.9 h3:garch_ar1:0.5 h3:ar2:0.5 h3:ar2:0.9
    uv run python docs/research/p8_simulations/validation_gate2.py h4:iid:1:0.05 h4:iid:5:0.08 h4:ar1_0.2:5:0.12   # など
