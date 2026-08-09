# Momentum Trader documentation

Use these documents in order:

1. [Monday runbook](MONDAY_RUNBOOK.md) — the exact first-session procedure.
2. [Strategy and profit mechanics](STRATEGY_AND_PROFIT.md) — how the app attempts to earn money,
   what must be true for it to work, and the dollar math for a $50 account.
3. [Safety, F-1, cash-account, and tax boundaries](SAFETY_AND_COMPLIANCE.md).
4. [Troubleshooting](TROUBLESHOOTING.md) — OAuth, zero balance, stale quotes, locked sessions, and
   rejected orders.
5. [Daily journal template](DAILY_JOURNAL_TEMPLATE.md) — record the evidence needed to decide
   whether the strategy has positive expectancy.

## One-sentence operating rule

Trade only when Robinhood and the app agree on the account, buying power, positions, orders, and
fresh quotes; authorize one bounded session; stop when the cap is reached or anything looks wrong.

Automation is execution discipline. It is not, by itself, a source of investment returns.

