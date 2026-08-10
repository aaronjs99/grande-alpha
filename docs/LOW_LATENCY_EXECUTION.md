# Low-latency execution profile

GRANDE Alpha can run a medium-frequency **research and observation** loop, but Robinhood Agentic
Trading is a remote MCP request/response interface—not a colocated exchange gateway or a documented
streaming direct-market-data feed. The GPU does not remove internet, provider, routing, or fill
latency. Faster order submission also does not create positive expectancy.

## Three independent clocks

| Clock | Default | Configurable | Purpose |
|---|---:|---:|---|
| Batched QQQ/TQQQ/SQQQ quote request | 1 s | 0.25-5 s | Observe the freshest provider snapshot available |
| Completed QQQ decision bar | 5 s | 1-300 s | Update the causal strategy signal |
| Portfolio/position/order reconciliation | 5 s | 2-60 s | Refresh broker account truth |

The quote loop is single-flight. If a request takes 1.4 seconds while the target is 0.25 seconds,
GRANDE Alpha does not queue five stale calls; it coalesces those ticks and starts again after the
active request completes. Account calls are issued sequentially so they do not pre-queue an entire
reconciliation batch ahead of a waiting quote request. Thus actual speed is approximately:

```text
effective quote rate <= 1 / max(configured interval, observed provider round-trip time)
decision rate <= min(fresh-quote rate, 1 / completed-bar interval)
order rate <= every independent risk and broker gate
```

The remote endpoint has not published a performance or order-rate SLA in the cited product overview.
Do not interpret the 0.25-second UI minimum as provider permission, guaranteed throughput, or fresh
250 ms market data.

Version 0.9 upgrades settings created by older releases to the default 1/5/5-second profile. After
that one-time migration, values selected in Settings are preserved.

## Order path remains deliberately slower

A signal is not an order. Before any live submission, the controller still requires a passing,
unexpired evidence certificate for the exact bar interval and settings, a time-limited account grant,
fresh and sufficiently narrow quotes, market-hours permission, available exposure and loss budget,
no open order, Robinhood's order review, a 12-second cooldown, and the session order-rate ceiling.
The default live envelope allows at most two submissions per minute. A submitted order is immediately
placed in the local open-order snapshot, then reconciled against Robinhood.

## Choosing a profile

- **Default retail low latency:** 1 s quotes, 5 s bars, 5 s reconciliation.
- **Fast shadow experiment:** 0.25-0.5 s quotes and 1-5 s bars. Measure quote timestamps, duplicate
  snapshots, provider errors, spread, modeled slippage, and CPU usage. No real orders.
- **Live review:** only the exact cadence that passed Evidence Lab under realistic costs and has a
  current certificate. A cadence change resets warm-up and changes the evidence fingerprint.

Start faster settings in live shadow for several complete sessions. Promote nothing based on local
throughput alone; the gates require out-of-sample economics after costs. Current packaged research
has no passing live strategy certificate.

## Current provider boundaries

Robinhood's [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
describes portfolio/account reads, quotes, order review, and order placement through Trading MCP, and
warns that automated strategies can move quickly and be difficult to stop. Robinhood's
[market-data explanation](https://robinhood.com/us/en/support/articles/using-market-data/) distinguishes
displayed market prices from consolidated quotes and notes session-specific delays and extended-hours
risks. GRANDE Alpha therefore treats quote timestamps, age, spread, and broker review as controls—not
as proof of direct-feed quality.
