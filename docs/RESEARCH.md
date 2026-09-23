# Research and evidence

GRANDE Alpha's included policies are experiments, not demonstrated trading edges. The mixed
candidate combines timestamped post-earnings observations for individual stocks with a bounded
QQQ/TQQQ/SQQQ allocation. ETF allocation and stock allocation have separate caps. Missing
earnings, price history, or sector information leaves the affected stock budget in cash rather
than increasing leveraged-ETF exposure.

## Data contracts

Record an earnings observation with the symbol, announcement time, actual result, the consensus
value available **before** the announcement, source, and capture time. A later consensus revision
is not valid preannouncement evidence. A provider response may be cached, but a refetch is a new
observation, not a replacement for the original. The Alpha Vantage collector uses a local request
budget; it cannot establish whether an endpoint or field is available under a particular account.

Historical simulations must distinguish actual broker-observed quotes from generated bars,
delayed data, and synthetic fixtures. Preserve source timestamps, exchange timezone, bid/ask,
corporate-action adjustment, and licensing constraints. The `cash_t1` sandbox keeps unsettled sale
proceeds in equity but does not make them available for immediate re-entry. Simulation fills are
not evidence of broker fills.

## Evaluation

Freeze strategy rules and order-sizing assumptions before evaluating a holdout. Replay with the
same market window, quote freshness, order route, spread, slippage, delay, partial-fill, and
settlement assumptions that the proposed runtime uses. Separate development and later chronological
holdout periods; report failures as well as passes. Forward shadow operation tests behavior and
operations, but cannot prove future profit or exact live fill prices.

Legacy Evidence Lab certificates and exact-runtime manifests are retained as historical research
records. They are not a mandatory profit gate for the mixed live route. Independent technical
checks for broker permission, exact approval, risk limits, durable recovery, and executable data
remain necessary. Crypto Agent Desk material remains exploratory; it is not part of the mixed
stock/ETF execution route.

## Historical findings

Dated reports and their images remain in [historical records](historical/README.md). In
particular, the [August baseline](historical/BASELINE_VALIDATION_2026-08-09.md),
[champion selection](historical/CHAMPION_SELECTION_2026-08-11.md), and
[strategy research](historical/STRATEGY_RESEARCH_2026-08-09.md) describe earlier versions and
cannot be treated as current performance claims. See the
[capability matrix](USER_GUIDE.md#what-is-implemented-tested-and-still-pending) for what the
current code actually implements and tests.
