# Baseline validation — 2026-08-09

This is a negative result and is intentionally published. It describes one reproducible research
run of the bundled default strategy; it is not investment advice, a forecast, or a complete market
study.

## Dataset and method

| Field | Value |
|---|---|
| Source | Unsupported Yahoo Finance public chart endpoint |
| Instruments | QQQ, TQQQ, SQQQ |
| Interval | 5 minutes |
| Window | 2026-06-11 13:30 UTC through 2026-08-07 20:00 UTC |
| Aligned bars | 3,121 |
| Market sessions | 40 |
| Missing aligned intraday intervals | 0 |
| SHA-256 dataset hash | `a273085fced5ef3105f8c08843a907baf5ceff0f32f32555cfae2f4722cdff8c` |

The run used the default `SandboxConfig`, 100 seeded random-entry trials, the compact neighboring
parameter grid, 1x/2x/3x modeled execution-cost stress, and five chronological walk-forward folds.
The endpoint is not a contracted or redistribution-approved feed; these aggregates do not validate
its accuracy or licensing.

## Result

| Metric | Observed |
|---|---:|
| Baseline return | -3.15% |
| Maximum drawdown | 3.59% |
| Closed round trips | 129 |
| Profit factor | 0.66 |
| Expectancy per round trip | -$0.0122 |
| Return at 3x modeled costs | -5.03% |
| Random-entry percentile | 73rd |
| Profitable neighboring configurations | 0% |
| Positive walk-forward folds | 40% |
| Out-of-sample round trips | 53 |
| Median out-of-sample profit factor | 0.45 |
| Median out-of-sample expectancy | -$0.0171 |
| Promotion status | `SHADOW_ONLY` |

The strategy failed parameter stability, cost stress, after-cost quality, random-control, and
walk-forward gates. It should not be represented as profitable or used to justify real-order
automation. This result is a reason to continue research, not a reason to loosen the gates or tune
against this same evaluation window.
