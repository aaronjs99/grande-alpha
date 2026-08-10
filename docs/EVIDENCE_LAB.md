# Evidence lab and promotion gates

The evidence lab is designed to make a promising backtest harder to fool. Its strongest outcome,
`LIVE_REVIEW_ELIGIBLE`, creates a local, 30-day certificate for the exact strategy fingerprint.
That certificate only makes the separate live-risk review available; it does not authorize an
order or predict future profit. The normal outcome is `SHADOW_ONLY`.

## Tests produced

- **Preset comparison:** every preset runs on the same immutable dataset.
- **Parameter sensitivity:** strategy-appropriate neighboring parameters show whether the result
  is broad or a single lucky point.
- **Cost stress:** the candidate reruns at 1x, 2x, and 3x spread, slippage, and commissions.
- **Random-entry control:** seeded random direction/entry trials provide a basic luck benchmark.
- **Walk-forward:** each fold selects a configuration using earlier training sessions and measures
  it only on later, non-overlapping test sessions.
- **Profit concentration:** checks whether one day accounts for most positive daily P/L.
- **Trial-adjusted significance:** applies a transparent one-sided normal approximation with a
  Bonferroni familywise correction across every sensitivity candidate. This is a conservative
  screen, not a Deflated Sharpe Ratio and not proof of a stable distribution.

## Current gates

| Gate | Requirement |
|---|---|
| Historical source | Observed or imported market history; synthetic scenarios are ineligible |
| Data breadth | At least 120 market sessions; use imported licensed history when the built-in source is shorter |
| Data recency | Final observation no more than 30 days old |
| Data integrity | Hash-valid, with zero duplicate or missing intraday intervals |
| Parameter stability | At least half of neighboring configurations profitable |
| Cost stress | Positive P/L at 3x modeled costs |
| Closed-trade sample | At least 30 after-cost round trips |
| After-cost quality | Profit factor at least 1.20 and positive expectancy |
| Random-entry control | Strategy at or above the 75th percentile of seeded random trials |
| Trial-adjusted significance | One-sided Bonferroni-adjusted daily-P/L p-value at most 0.05 |
| Profit concentration | No single day over 50% of positive daily P/L |
| Drawdown | No more than 5% in the research configuration |
| Ending flat | No virtual position remains open at the end of replay |
| Exact candidate identity | Every training fold selected the exact configuration being certified |
| Walk-forward | At least five folds, 60% positive test folds, 20 out-of-sample trades, median profit factor 1.10, and positive median expectancy |

All gates must pass simultaneously. Every pass and failure is stored in the local audit database.
Changing a fingerprinted signal, exit setting, bar interval, or policy version invalidates the
certificate. A requested live grant cannot exceed the notional, exposure, loss, trade-rate, or
spread envelope tested by the certificate, and a certificate older than 30 days is ineligible. A pass does not estimate the probability of future profit,
validate the market-data license, account for tax/settlement restrictions, or authorize real money.
Before any live review, preserve a final dataset period that was not used to invent, select, or tune
the strategy.

## Avoiding false evidence

- Do not choose the best configuration and then describe its training return as out-of-sample.
- Do not rerun the final holdout repeatedly until it passes.
- Do not ignore rejected fills, open ending positions, costs, negative days, or zero-trade folds.
- Do not compare two configurations on different hashes.
- Record failed experiments as well as successful ones.
