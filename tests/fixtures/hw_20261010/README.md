# Hardware fixture (2026-10-10)

`deals.csv` is the canonical deal list (content sha256 `dae78cda…`) of the P1/P2 E2E run:
`RL_SmokeTest` (12/48, 0.01 lot) on EURUSD H1, `[2023-01-02, 2024-01-01)`, Model=4, MT5 build 6251,
MetaQuotes-Demo. It contains deal data only (no account information).

MT5 TesterStatistics of the same run (from `rlab metrics show`, job `jb_20261010114318457_9fcebf4b`):
profit 47.16, gross_profit 335.07, gross_loss -287.91, trades 155, profit_trades 62, loss_trades 93,
profit_factor 1.16, expected_payoff 0.30, balance_dd 70.69, equity_dd 85.11, recovery_factor 0.55,
max_conwins 19.99, max_conprofit_trades 4, max_conlosses -29.35, max_conloss_trades 10.
EA-tracked equity max drawdown: 85.33. There are two longest win runs (length 4): 19.41 then 19.99.
