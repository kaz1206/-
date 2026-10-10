# MT5 EA Robustness Lab（設計段階）

MT5 Strategy Tester をバックテストエンジンとして利用し、EA のロバスト性・過学習耐性・DD/破産リスクを体系的に評価し、
最終的に EA ごとの資金配分まで決定する研究・検証基盤です。

現在は **P1（MT5 単一バックテストの安全な実行と再現可能な保存）** が完了しています（2026-10-10 実機確認済み）。Windows 実機での確認手順は [docs/runbooks/P1_E2E.md](docs/runbooks/P1_E2E.md)。

- 設計書: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 調査結果（手法評価・MT5 仕様確認）: [docs/research/FINDINGS.md](docs/research/FINDINGS.md)
- 情報源レジストリ: [docs/research/SOURCES.md](docs/research/SOURCES.md)
- 設計変更履歴: [docs/DESIGN_CHANGELOG.md](docs/DESIGN_CHANGELOG.md)
