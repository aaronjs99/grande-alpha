# Evidence lab and promotion gates

The evidence lab is designed to make a promising backtest harder to fool. It never enables live
trading. Its strongest outcome, `LIVE_REVIEW_ELIGIBLE`, means only that a human may perform a new
live-risk review. The normal outcome is `SHADOW_ONLY`.

## Tests produced

- **Preset comparison:** every preset runs on the same immutable dataset.
- **Parameter sensitivity:** neighboring fast/slow EMA and threshold choices show whether the
  result is broad or a single lucky point.
- **Cost stress:** the candidate reruns at 1x, 2x, and 3x spread, slippage, and commissions.
- **Random-entry control:** seeded random direction/entry trials provide a basic luck benchmark.
- **Walk-forward:** each fold selects a configuration using earlier training sessions and measures
  it only on later, non-overlapping test sessions.
- **Profit concentration:** checks whether one day accounts for most positive daily P/L.

## Current gates

| Gate | Requirement |
|---|---|
| Historical source | Observed or imported market history; synthetic scenarios are ineligible |
| Data breadth | At least 20 market sessions |
| Data integrity | Hash-valid, with zero duplicate or missing intraday intervals |
| Parameter stability | At least half of neighboring configurations profitable |
| Cost stress | Positive P/L at 2x modeled costs |
| Profit concentration | No single day over 50% of positive daily P/L |
| Drawdown | No more than 5% in the research configuration |
| Walk-forward | At least five folds and at least 60% positive test folds |

All gates must pass simultaneously. A pass does not estimate the probability of future profit,
validate the market-data source, account for tax/settlement restrictions, or authorize real money.
Before any live review, preserve a final dataset period that was not used to invent, select, or tune
the strategy.

## Avoiding false evidence

- Do not choose the best configuration and then describe its training return as out-of-sample.
- Do not rerun the final holdout repeatedly until it passes.
- Do not ignore rejected fills, open ending positions, costs, negative days, or zero-trade folds.
- Do not compare two configurations on different hashes.
- Record failed experiments as well as successful ones.
