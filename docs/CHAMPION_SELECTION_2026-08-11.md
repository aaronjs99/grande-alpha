# Runtime champion selection — 2026-08-11

## Decision

The current normal and scheduled runtime champion is **CASH / hold**. It emits a flat signal,
requests the `(0,0)` pair action when no position is held, and creates no TQQQ or SQQQ entry.

This is not a claim that cash makes trading profit. It is the highest-returning admissible choice in
the current intraday comparison because every strategy candidate lost money after modeled costs.
The application must not replace cash merely to create activity.

## Development protocol

- Source: locally cached, aligned QQQ/TQQQ/SQQQ 5-minute community history.
- Available span: 40 regular-market sessions.
- Untouched reserve: the final five sessions, preceded by one purged session. That terminal block
  was not evaluated, claimed, consumed, or promoted.
- Development evaluation: three non-overlapping chronological folds, each with 15 training
  sessions, one purged session, and five later test sessions.
- Account model: $50 starting cash, $25 order cap, `cash_t1` settlement, and forced session-flat
  research handling.
- Stress: three times the modeled slippage and both static and volatility-driven spread components.
- Selection boundary: no model may be promoted from this small development sample. Evidence policy
  v9 also blocks non-cash promotion until replay and runtime share the same certified sizing contract.

The normalized OOS figure below compounds independently reset fold returns for comparison. It is
not a literal fixed-$25 account trajectory.

| Candidate family | Positive folds | Normalized OOS | Test trades | Worst fold drawdown |
|---|---:|---:|---:|---:|
| CASH / hold | 3/3 non-losing | 0.00% | 0 | 0.00% |
| Best EMA grid candidate | 0/3 | -0.60% | 6 | 0.32% |
| Closing-window momentum | 0/3 | -1.02% | 14 | 0.80% |
| First-half-hour momentum | 0/3 | -1.49% | 10 | 1.08% |
| Conservative ensemble | 0/3 | -1.92% | 28 | 1.28% |
| Fixed EMA 8/21/4 bps | 0/3 | -2.36% | 43 | 1.18% |
| Opening-range breakout | 0/3 | -2.60% | 42 | 1.49% |
| Multi-horizon trend | 0/3 | -2.64% | 31 | 1.16% |

Cash is not credited with statistical evidence or a live certificate: it has no closed trades,
positive expectancy, or after-cost profit. It is simply the fail-safe runtime state while all
directional candidates fail.

## What the long history does—and does not—support

The local daily cache contains 4,147 aligned sessions beginning in February 2010. A separate
research-only next-open/T+1 audit found that long/cash QQQ trend filters reduced drawdown relative
to persistent TQQQ exposure and were historically positive. Those results mostly reflect long
Nasdaq beta through a strong sample; they do not establish independent alpha, an intraday edge, or
permission to automate overnight exposure. Persistent SQQQ and dual TQQQ/SQQQ policies performed
materially worse.

The next research candidate may therefore be a separately versioned TQQQ/cash daily trend filter.
It must have an explicit next-session execution model, overnight risk rules, its own fingerprint,
and a future sealed holdout before it can affect runtime.

## Why the app does not optimize harder

Searching more parameter combinations increases the chance of finding a lucky backtest. GRANDE
Alpha records each unique trial, applies purged chronological evaluation and Deflated Sharpe, and
reserves a later one-use holdout. The final holdout must never be used to choose a feature,
threshold, cost model, or strategy family.

Relevant primary research includes:

- [Volatility-Managed Portfolios](https://www.nber.org/papers/w22208) for causal volatility scaling
  at a much slower horizon—not validation of this intraday product.
- [Market intraday momentum](https://www.sciencedirect.com/science/article/pii/S0304405X18301351)
  for the specific first-half-hour/last-half-hour hypothesis—not all-day five-second momentum.
- [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) for
  multiple-testing and non-normal-return correction.
- Official [TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) and
  [SQQQ](https://prod.proshares.com/our-etfs/leveraged-and-inverse/sqqq) disclosures for the funds'
  daily objectives and path-dependent longer-horizon behavior.

None of these sources promises profit or validates GRANDE Alpha's exact retail data and execution
stack. A future non-cash champion must win after realistic costs on later data and then survive
forward shadow operation. Until then, the correct automated action is hold.
