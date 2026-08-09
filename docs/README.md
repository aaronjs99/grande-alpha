# GRANDE Alpha documentation

Use these documents in order:

1. [Monday runbook](MONDAY_RUNBOOK.md) — the exact first-session procedure.
2. [Strategy and profit mechanics](STRATEGY_AND_PROFIT.md) — how the app attempts to earn money,
   what must be true for it to work, and the dollar math for a $50 account.
3. [Safety, F-1, cash-account, and tax boundaries](SAFETY_AND_COMPLIANCE.md).
4. [Troubleshooting](TROUBLESHOOTING.md) — OAuth, zero balance, stale quotes, locked sessions, and
   rejected orders.
5. [Daily journal template](DAILY_JOURNAL_TEMPLATE.md) — record the evidence needed to decide
   whether the strategy has positive expectancy.
6. [System architecture](SYSTEM_ARCHITECTURE.md) — how GRANDE Alpha relates to the wider GRANDE
   project without entering the robot runtime.
7. [GRANDE Research Fund](GRANDE_RESEARCH_FUND.md) — a confirmation-gated personal-contribution
   ledger that never transfers money.
8. [Sandbox](SANDBOX.md) — configurable TQQQS/SQQQS replay with data hashes, realistic execution,
   virtual accounting, replay inspection, and strict separation from Robinhood.
9. [Evidence lab](EVIDENCE_LAB.md) — comparisons, sensitivity, cost stress, random controls,
   walk-forward testing, and conservative promotion gates.
10. [Live shadow mode](SHADOW_MODE.md) — current quotes and signals with virtual fills and no broker
    order authority.

## One-sentence operating rule

Trade only when Robinhood and the app agree on the account, buying power, positions, orders, and
fresh quotes; authorize one bounded session; stop when the cap is reached or anything looks wrong.

Automation is execution discipline. It is not, by itself, a source of investment returns.
