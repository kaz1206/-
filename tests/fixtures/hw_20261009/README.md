# Hardware fixture (2026-10-09)

`deals.csv` is the unmodified deal list written by the verification EA in run R1
(MT5 build 6230, MetaQuotes-Demo, EURUSD H1, 2023.01.09–2023.01.13, Model=4).
It has the same column layout as the P1 telemetry (`mql5/Include/RobustLab/Telemetry.mqh`).

Observed values used by `tests/telemetry_factory.py` for the matching stats/env files:
STAT_PROFIT 9.69, STAT_DEALS 64, deals_total 65 (1 balance + 64 trades),
first tick 2023.01.09 00:02:40, last tick 2023.01.12 23:59:52, 467,749 ticks, 96 bars.
See `verification/p1/RECORD.md`.
