# Nine-action offline policy lab

The Action Lab is an isolated research tool. It cannot submit, review, or unlock live orders.
It represents the user's requested command space exactly:

| TQQQS command \\ SQQQS command | -1 sell | 0 hold | +1 buy |
|---|---:|---:|---:|
| **-1 sell** | (-1,-1) | (-1,0) | (-1,+1) |
| **0 hold** | (0,-1) | (0,0) | (0,+1) |
| **+1 buy** | (+1,-1) | (+1,0) | (+1,+1) |

## What sell means

The public preview is long-only. A sell reduces an existing one-unit sandbox position; it does
not open a short. A buy increases a leg from zero to one unit. Commands that would create negative
inventory or exceed one unit are masked. Buying both legs is allowed, but it is not treated as
diversification: both daily-reset funds can lose value over time and the pair pays two sets of
trading costs. Selling TQQQS and buying SQQQS can both reduce bullish exposure, but they are not
economically identical: the first removes an existing asset, while the second creates inverse-fund
exposure with its own path dependence, spread, tax lot, and capital requirement.

## State, training, and reward

The compact state is

```text
x_t = (20-day QQQ trend bucket, 20-day realized-volatility bucket,
       TQQQS inventory, SQQQS inventory)
```

The policy uses tabular Q-learning on the earlier 70% of aligned daily transitions. Training
exploration decays by epoch. The remaining 30% is evaluated once, later in time, without learning.
For action `a_t`, resulting holdings `h_T` and `h_S`, equal leg weight `w = 0.5`, turnover `u_t`,
and cost `c = 8 bps`, the causal one-day reward is

```text
r_(t+1) = w[h_T(P^T_(t+1)/P^T_t - 1) + h_S(P^S_(t+1)/P^S_t - 1)]
          - w u_t c

Q(x_t,a_t) <- Q(x_t,a_t) + alpha [r_(t+1)
                  + gamma max_a Q(x_(t+1),a) - Q(x_t,a_t)]
```

The decision sees the current close only. Reward uses the subsequent close, so overnight risk is
included and future opens are not leaked into the decision. Every holdout command, inventory
transition, reward, and resulting equity value is displayed and recorded locally.

## Frozen full-history result

The 2026-08-09 run used 4,147 aligned daily observations from 2010-02-11 through 2026-08-07,
dataset hash prefix `da00f6f963bb1cbc`, 2,882 training transitions, and 1,244 untouched holdout
transitions from 2021-08-24 through 2026-08-07. With the default cost and hyperparameters, the
greedy holdout policy selected `(0,0)` on every day. Return was **0.00%** and maximum drawdown was
**0.00%**. This is a failed edge test: the learner found no action with sufficient estimated value
to displace cash. It must not be retuned against that same holdout and then described as out of
sample. The application also displays passive QQQ, TQQQ, and SQQQ close-to-close holdout returns
as context; those are uncosted benchmarks, not alternative strategy recommendations.

| Frozen holdout comparison | Return |
|---|---:|
| Learned nine-action policy after modeled costs | 0.00% |
| Passive QQQ, no modeled costs | +93.72% |
| Passive TQQQ, no modeled costs | +110.00% |
| Passive SQQQ, no modeled costs | -96.13% |

## What would improve the evidence

Improvements should strengthen validity before complexity: multiple purged walk-forward folds,
an embargo around fold boundaries, explicit buy-and-hold and random-entry baselines, parameter-
neighborhood stability, a ledger of every attempted model, and trial-adjusted significance. An
ensemble may average separately trained fold policies only after standardizing their comparable
outputs. Intraday work needs licensed high-quality bars and a materially richer fill model.

Deep reinforcement learning is not automatically better here. Roughly four thousand daily rows
are small for a neural policy, TQQQ/SQQQ are highly correlated transformations of one underlying
index, and repeated tuning can manufacture attractive backtests. GPU capacity does not create
statistical information. No model should advance unless later, untouched evidence passes the
existing Evidence Lab gates after realistic costs.

## Daily exposure benchmarks

Version 0.8 adds causal benchmarks beginning after 200 warm-up sessions. A 20-day trailing TQQQ
volatility estimate sizes the next close-to-close exposure; the target is 20% annualized volatility,
the TQQQ weight is capped at 50%, and changes pay 8 bps of modeled cost. The SMA200 variant uses
only the current and prior QQQ closes. These are full-sample exposure comparisons, not holdout
alpha tests. Four volatility targets were explored during development, so the 20% row must not be
described as untouched.

| Full-history benchmark | Return | CAGR | Max DD | Sharpe |
|---|---:|---:|---:|---:|
| Cash | 0.0% | 0.0% | 0.0% | 0.00 |
| Fixed 50% TQQQ | +2,977.3% | 24.4% | 51.8% | 0.87 |
| 20% volatility-managed TQQQ | +1,325.6% | 18.4% | 29.8% | 0.95 |
| 20% volatility-managed TQQQ with QQQ SMA200 gate | +726.6% | 14.4% | 24.6% | 0.84 |

The volatility-managed row improved historical risk-adjusted performance and drawdown relative to
fixed 50% exposure, but its absolute return was lower. Both are dominated by Nasdaq market beta,
daily leverage, and this sample's long technology bull market. Neither establishes skill.
