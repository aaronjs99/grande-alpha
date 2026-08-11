# Evidence lab and promotion gates

The evidence lab is designed to make a promising backtest harder to fool. Its strongest outcome,
`LIVE_REVIEW_ELIGIBLE`, creates a local, 30-day certificate for the exact strategy fingerprint.
That certificate only makes the separate live-risk review available; it does not authorize an
order or predict future profit. The normal outcome is `SHADOW_ONLY`.

## Tests produced

- **Preset comparison:** every preset runs on the same immutable dataset.
- **Parameter sensitivity:** strategy-appropriate neighboring parameters show whether the result
  is broad or a single lucky point.
- **Cost stress:** the candidate reruns at 1x, 2x, and 3x slippage, commissions, and both
  the static and volatility-driven spread components.
- **Random-entry control:** seeded random direction/entry trials provide a basic luck benchmark.
- **Purged walk-forward:** each fold selects a configuration using earlier training sessions,
  leaves a configurable session gap, and measures it only on later test sessions.
- **Profit concentration:** checks whether one day accounts for most positive daily P/L.
- **Trial ledger:** unique candidate fingerprints are committed by dataset before promotion is
  evaluated, so previously registered trials cannot disappear from the reported search count.
- **Trial-adjusted significance:** applies both a Bonferroni familywise correction and the
  Deflated Sharpe Ratio's selection-bias, skew, and kurtosis adjustment.
- **One-use final holdout:** evidence-policy version 8 reserves a later chronological block before
  candidate evaluation, freezes it to the selected strategy fingerprint, claims it before reading
  its result, and records that result permanently after one evaluation.

## Current gates

| Gate | Requirement |
|---|---|
| Historical source | Observed or imported market history; synthetic scenarios are ineligible |
| Trading-session coverage | Dataset covers the complete regular, extended, or 24-hour session selected by the strategy |
| Data breadth | At least 120 market sessions; use imported licensed history when the built-in source is shorter |
| Data recency | Final observation no more than 30 days old |
| Data integrity | Hash-valid, zero duplicate/missing intraday intervals, and at least 95% complete selected sessions |
| Runtime sizing parity | Replay and runtime use the exact same certified sizing contract. This currently fails every non-cash candidate because replay applies risk-budget and volatility sizing that shadow/live do not share. Cash passes only with zero fills and zero exposure, then still fails the trade-sample and profit gates. |
| Parameter stability | At least half of neighboring configurations profitable |
| Cost stress | Positive P/L at 3x modeled costs |
| Closed-trade sample | At least 30 after-cost round trips |
| After-cost quality | Profit factor at least 1.20 and positive expectancy |
| Random-entry control | Strategy at or above the 75th percentile of seeded random trials |
| Trial-adjusted significance | One-sided Bonferroni-adjusted daily-P/L p-value at most 0.05 |
| Deflated Sharpe | At least 95% probability after registered-trial and non-normality adjustment |
| Profit concentration | No single day over 50% of positive daily P/L |
| Drawdown | No more than 5% in the research configuration |
| Ending flat | No virtual position remains open, and the result did not rely on the simulator's failure-bypassing forced close |
| Exact candidate identity | Every training fold selected the exact configuration being certified |
| Walk-forward | At least five folds, 60% positive test folds, 20 out-of-sample trades, median profit factor 1.10, and positive median expectancy |
| Sealed final holdout | The frozen candidate passes one later, purged, one-use holdout at 3x modeled costs with positive P/L, at least 5 round trips, profit factor at least 1.10, positive expectancy, drawdown no more than 5%, and an ending-flat result that did not rely on the simulator's failure-bypassing forced close |

All gates must pass simultaneously. Every pass and failure is stored in the local audit database.
Changing a fingerprinted signal, exit setting, bar interval, decision stride, execution session,
order type, time in force, limit offset, or policy version invalidates the
certificate. A requested live grant cannot exceed the notional, exposure, loss, trade-rate, or
spread envelope tested by the certificate, and a certificate older than 30 days is ineligible. A pass does not estimate the probability of future profit,
validate the market-data license, account for tax/settlement restrictions, or authorize real money.
Before any live review, preserve a final dataset period that was not used to invent, select, or tune
the strategy.

## Policy v8 final-holdout lifecycle

The seal is an auditable database lifecycle, not encryption. The app records the full dataset hash,
development hash, holdout hash and dates, policy version, and selected strategy fingerprint.

```text
RESERVED -> FROZEN -> EVALUATING -> CONSUMED
```

- `RESERVED` fixes the later chronological block before candidate evaluation begins. If the
  development-only gates fail, the block remains reserved and unread rather than being wasted.
- `FROZEN` binds that block to exactly one selected strategy fingerprint, and occurs only after all
  non-holdout gates pass.
- `EVALUATING` is claimed atomically before the holdout result is calculated, so a crash or bad
  result cannot make the same block look unused.
- `CONSUMED` stores the metrics whether the gate passes or fails. The same dataset/date block cannot
  be reserved again under another candidate or policy version.

At the live-authority boundary, storage independently requires the complete canonical gate set,
recomputes the final-holdout thresholds from the consumed metrics, binds the full dataset and final
timestamp to the holdout record, and permits at most one promotion receipt for that holdout. Caller-
supplied `passed` labels alone are never sufficient.

Changing a parameter after the reveal creates a new candidate; it does not justify another attempt
on the consumed block. A new evaluation requires genuinely later untouched data and a new sealed
holdout. Walk-forward folds and neighboring-parameter tests remain development evidence and do not
replace this final one-use test.

## Avoiding false evidence

- Do not choose the best configuration and then describe its training return as out-of-sample.
- Do not inspect, retune on, or rerun the final holdout. Under policy v9, a claimed holdout is used
  once and remains consumed even when it fails.
- Do not ignore rejected fills, open ending positions, costs, negative days, or zero-trade folds.
- Do not compare two configurations on different hashes.
- Record failed experiments as well as successful ones.
