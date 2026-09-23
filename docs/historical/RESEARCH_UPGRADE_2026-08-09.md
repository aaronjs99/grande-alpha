# GRANDE Alpha 0.8 research and validation upgrade

## Decision

No researched forecasting strategy is ready for real-money promotion. The best corrected
40-session intraday candidate is still negative after modeled costs. Long-run TQQQ exposure was
historically profitable, and volatility management reduced its drawdown, but this is compensated
Nasdaq beta rather than demonstrated trading alpha.

## Primary-source findings

1. Gao, Han, Li, and Zhou's [market intraday momentum study](https://doi.org/10.1016/j.jfineco.2018.05.009)
   finds that the return from the prior close through the first half hour predicts the final half
   hour. The older GRANDE strategy used the rest-of-day return, so version 0.8 adds a separate
   source-faithful implementation and stops treating the old proxy as the paper's rule.
2. Moreira and Muir's [volatility-managed portfolios](https://www.nber.org/papers/w22208) reduce
   exposure when trailing variance rises. GRANDE applies this only as a causal sizing benchmark;
   it is not represented as a proprietary return forecast.
3. Moskowitz, Ooi, and Pedersen's [time-series momentum](https://doi.org/10.1016/j.jfineco.2011.11.003)
   documents one-to-twelve-month persistence across diversified futures, not proof that one
   leveraged Nasdaq ETF can be timed profitably.
4. Bailey and López de Prado's [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
   corrects for multiple strategy selection and non-normal returns. Version 0.8 implements this
   alongside a persistent unique-trial ledger and the existing Bonferroni gate.
5. ProShares states that [TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) and
   [SQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/sqqq) target daily leveraged
   returns. Longer holding-period outcomes can differ significantly from the daily target, so
   every comparison uses actual fund prices and reports drawdown rather than assuming exact 3x
   compounding.

## Corrected intraday evidence

Frozen unsupported Yahoo research data: 40 sessions, 3,121 aligned five-minute bars, 2026-06-11
through 2026-08-07, SHA-256 `a273085fced5ef3105f8c08843a907baf5ceff0f32f32555cfae2f4722cdff8c`.

| Candidate | Return | Max DD | Profit factor | Round trips |
|---|---:|---:|---:|---:|
| Opening breakout | -0.66% | 2.43% | 0.93 | 101 |
| First-half-hour momentum | -0.77% | 3.08% | 0.84 | 35 |
| Slow/selective EMA | -2.14% | 3.80% | 0.70 | 78 |
| Rest-of-day closing momentum | -2.94% | 4.59% | 0.54 | 40 |
| Fast/reactive EMA | -4.34% | 4.54% | 0.30 | 87 |
| Balanced EMA | -4.78% | 5.00% | 0.52 | 126 |
| Multi-horizon trend | -4.83% | 5.05% | 0.40 | 104 |
| Agreement ensemble | -5.27% | 5.56% | 0.40 | 103 |

For the source-faithful first-half-hour rule, all three threshold neighbors lost money, the 3x-cost
return was -2.60%, random-control percentile was 62, and the three purged walk-forward folds were
all negative. Deflated-Sharpe probability was 0.2609, far below the 0.95 gate. This falsifies the
candidate on the available sample; it does not prove the published effect never exists elsewhere.

## Long-run exposure benchmarks

Full shared daily history contains 4,147 aligned observations from 2010-02-11 through 2026-08-07.
The table begins after 200 warm-up sessions and uses actual TQQQ closes. Volatility-managed rows use
a causal 20-day TQQQ volatility estimate, a 20% annual target, a 50% maximum portfolio weight, and
8 bps on changes in weight.

| Benchmark | Return | CAGR | Max DD | Sharpe |
|---|---:|---:|---:|---:|
| Fixed 50% TQQQ | +2,977.3% | 24.4% | 51.8% | 0.87 |
| 20% volatility-managed TQQQ | +1,325.6% | 18.4% | 29.8% | 0.95 |
| Volatility-managed plus QQQ SMA200 | +726.6% | 14.4% | 24.6% | 0.84 |

The fixed exposure produced the largest terminal wealth and the largest drawdown. Volatility
management improved Sharpe and reduced drawdown while sacrificing return. The SMA gate reduced
drawdown further but did not improve Sharpe. Four target-volatility levels were examined during
research, so these rows are exploratory full-sample comparisons, not untouched tests.

## What is novel here

The product contribution is the evidence architecture rather than a claimed new anomaly:

- signal on unlevered QQQ and execute using actual leveraged-fund history;
- exact intraday timing aligned to the source hypothesis;
- causal volatility sizing as a separate risk layer;
- purged walk-forward boundaries;
- persistent dataset-specific candidate ledger;
- Deflated Sharpe plus Bonferroni, cost, random-entry, concentration, drawdown, and identity gates;
- automatic invalidation through evidence-policy version 5.

Any genuinely new return hypothesis must now be preregistered in the trial ledger before its result
is inspected. The next decision-grade milestone is at least 120 licensed sessions plus forward
shadow data that were not used in this development cycle. GPU optimization or deep reinforcement
learning cannot substitute for that missing independent information.
