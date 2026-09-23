# GRANDE Alpha system architecture

GRANDE Alpha uses explicit trust boundaries: strategies propose, risk controls authorize, broker
adapters communicate, storage records, and the UI obtains consent. Research components cannot reach
the broker write boundary.

## Core control pattern

| Control concept | GRANDE Alpha implementation |
|---|---|
| Strategy proposes an action | Deterministic strategy produces a trade intent |
| Independent runtime bounds | Risk engine independently approves or rejects the intent |
| Freshness and lifecycle gates | Quote age, spread, market-time, and session-state checks |
| External-effect boundary | Broker adapter review and order submission |
| Emergency stop | Pause/revoke only block local authority; `STOP + CANCEL` previews exact GRANDE-owned orders and requires confirmation before cancellation |
| Evidence trail | Frozen hash-chained authority receipts plus SQLite decision/broker receipts |
| Candidate versus approved runtime | `LOCKED`, `LIVE`, `EXPIRED`, and review-blocked states |
| Simulation boundary | `TQQQS`/`SQQQS` replay engine receives no broker object or live authority |
| Candidate observation | Live shadow consumes current quotes but has no broker-order dependency |
| Low-latency observation | Single-flight quote loop is independent of slower account reconciliation |
| Settlement-aware accounting | `cash_t1` separates settled cash from next-session sale proceeds |
| Final acceptance test | The current policy binds one later holdout to one fingerprint and consumes it once |

## Hard separation

- No money the operator does not own or is not authorized to allocate may enter the brokerage
  account or the capital planning ledger.
- No private, proprietary, unlawfully obtained, or materially nonpublic information may become a
  trading signal.
- Research, broker reads, live shadow, supervised placement, and autonomous authority are separate
  capability layers.
- Simulation, backtest, evidence, and live performance claims remain explicitly distinguished.
- The optional capital-planning feature records intended and externally confirmed contributions; it
  never transfers money.

Real-order capability and real-money authority are different layers. Configuration may remember that
session controls are available, but never stores a money-moving grant. The immutable grant binds the
exact account, ticker tuple, route, strategy fingerprint, ET-day expiry, and all risk ceilings. The
risk engine owns pause/revoke state, gross-notional reservations, and a hash-chained in-memory action
receipt queue. The controller supplies current binding context, persists receipts append-only, and
releases abandoned reservations. See [Bounded autonomous authority](UNATTENDED_ENGINE.md).

Cancellation is a separate consent boundary. The controller may lock new local requests at any time,
but its only cancellation path consumes a short-lived, exact preview of GRANDE-owned nonterminal
Agentic orders after explicit user confirmation. The preview is bound to the selected account and
order set; manual/unrelated orders are excluded, and pending-cancel orders are verification-only.
Revoke, Settings, Disconnect, credential forgetting, Exit, and internal fault paths cannot call the
broker cancellation operation. A reachable broker connection blocks verified Disconnect while owned
open or unresolved state remains. Exit and a local detach after transport loss stop local execution
without claiming broker cleanup; durable records remain for later reconciliation.

The sandbox and live-shadow executor share a pure decision policy with live automation. The policy
returns a target and reason; three separate execution boundaries consume that decision. Historical
replay writes virtual accounting tables, live shadow records virtual receipts from current quotes,
and only the live controller may request an official Robinhood review and order. Shadow and live
authority are mutually exclusive.

The runtime uses four clocks. A batched quote request targets the configured fast cadence and drops
overlapping timer ticks. Completed QQQ bars update analysis, while a slower integer bar stride selects
one exact `(T,S)` pair action, enforcing `t_analysis < t_trade`. Portfolio, position, and order truth
is reconciled separately. Broker review, open-order detection, a 12-second submission cooldown, and
the session's orders-per-minute limit remain independent gates. Two-leg rotations sell first and wait
for fill/reconciliation before buying; commands are not treated as atomic. This is designed to remain
stable when remote latency exceeds the local timer; it is not an exchange feed or colocated execution engine.

The live 5-second analysis bars are derived locally from the quote midpoints the request/response
loop observes. They are not provider-native 5-second historical candles. The current remote-history
adapter accepts 1-minute bars at its finest interval, so native 1-minute research and locally derived
5-second observation have different timing provenance and fingerprints. A data adapter must not
manufacture 5-second evidence by repeating or interpolating 1-minute candles.

Replay and live shadow default to the `cash_t1` settlement ledger. Buys reduce settled cash; sale
proceeds increase unsettled cash; total equity includes both; and only the next observed market
session transfers unsettled cash back to settled cash. This is deliberately separate from broker
account reconciliation. Actual buying power and settlement restrictions remain broker authority.

The research sandbox selects a finite strategy through a versioned factory. Each strategy accepts
completed QQQ bars and returns the same bullish, bearish, or flat signal contract. The factory does
not provide broker access. Live automation remains the explicit EMA baseline; a research strategy's
different fingerprint cannot authorize that live path.

The current evidence policy retains the durable final-holdout state machine and binds the complete
execution/sizing contract. A later chronological block
is reserved by dataset/date hash. The candidate is frozen only after every development-only gate
passes; the block is then atomically claimed before evaluation, and its metrics are consumed after
that single attempt. Storage revalidates the canonical gate set, dataset binding, 3x-cost holdout
metrics, policy version, and one-promotion limit before exposing live-review eligibility. A failed
or interrupted reveal cannot be silently reset and optimized away. The seal is audit state and
content identity, not encryption; promotion still depends on lawful, accurate source data and every
other Evidence Lab gate.

## Stock execution in the mixed engine

The foreground mixed runner uses `EquityScope`, `EquityOrderIntent`, `assess_ticket`, and
`EquityLedger` through `MixedExecutionEngine`. This is a code path, not evidence of a
provider-qualified deployment or a profitable strategy. The old ETF desktop grant is separate
and cannot authorize a stock order.

The scope binds the exact account, symbols, interval (at most seven days), and risk ceilings.
Stock intents use exact UUID references and decimal amounts; sells require share quantities.
Preflight checks account and eligibility truth, every held asset's quote and exposure, cash,
staleness, unresolved orders, daily usage, loss stop, and inventory-backed sells. The ledger
records the reference before dispatch, permits one outstanding intent per account, deduplicates
fills, and quarantines ambiguous placement outcomes. It does not infer an order identity from
symbol, price, or time similarity.

The CLI wires the scope to an exact candidate digest, qualification certificate, separate user
permit, earnings verifier, broker-eligibility adapter, and durable process lease. A fresh,
interactive arming step is required unless recovery finds the same sole active, unexpired grant.
See [live activation](LIVE_ACTIVATION.md) and
[production qualification](PRODUCTION_QUALIFICATION.md) for the remaining provider-observed
and deployment requirements.

## Low-latency execution profile

GRANDE Alpha can run a medium-frequency **research and observation** loop, but Robinhood Agentic
Trading is a remote MCP request/response interface—not a colocated exchange gateway or a documented
streaming direct-market-data feed. The GPU does not remove internet, provider, routing, or fill
latency. Faster order submission also does not create positive expectancy.

### Four independent clocks

| Clock | Default | Configurable | Purpose |
|---|---:|---:|---|
| Batched QQQ/TQQQ/SQQQ quote request | 1 s | 0.25-5 s | Observe the freshest provider snapshot available |
| Completed QQQ analysis bar (`t_analysis`) | 5 s | 1-300 s | Update the causal strategy signal |
| Pair-action decision (`t_trade`) | 15 s | 2-120 analysis bars | Select one `(T,S)` command using only completed analysis |
| Portfolio/position/order reconciliation | 5 s | 2-60 s | Refresh broker account truth |

The quote loop is single-flight. If a request takes 1.4 seconds while the target is 0.25 seconds,
GRANDE Alpha does not queue five stale calls; it coalesces those ticks and starts again after the
active request completes. Account calls are issued sequentially so they do not pre-queue an entire
reconciliation batch ahead of a waiting quote request. Thus actual speed is approximately:

```text
effective quote rate <= 1 / max(configured interval, observed provider round-trip time)
analysis rate <= min(fresh-quote rate, 1 / completed-analysis-bar interval)
pair-action rate <= analysis rate / configured decision stride
order rate <= every independent risk and broker gate
```

The remote endpoint has not published a performance or order-rate SLA in the cited product overview.
Do not interpret the 0.25-second UI minimum as provider permission, guaranteed throughput, or fresh
250 ms market data.

Version 0.11 upgrades settings created by older releases to the default 1-second quote, 5-second
analysis, 3-analysis-bar trade decision, and 5-second reconciliation profile. Thus nominal
`t_analysis=5s < t_trade=15s`. Cadence schema v5 also migrates legacy runtime configs with no
strategy field to the fail-safe `cash` champion. After that one-time migration, values selected in
Settings are preserved.

At each trade tick, the controller takes the newest completed analysis state whose timestamp is no
later than the trade tick. It records one command from the exact nine-action vocabulary. The
long-only inventory and risk mask can make some commands infeasible from a particular state. A sell
reduces an existing holding and never creates a short. A two-leg rotation is executed sells first,
then waits for broker fill/reconciliation before any buy; pair commands are not assumed atomic.

### Order path remains deliberately slower

A signal is not an order. Before any live submission, the controller still requires a passing,
unexpired evidence certificate for the exact bar interval, decision stride, and settings, a time-limited account grant,
fresh and sufficiently narrow quotes, market-hours permission, available exposure and loss budget,
no open order, Robinhood's order review, a 12-second cooldown, and the session order-rate ceiling.
The default live envelope allows at most two submissions per minute. A submitted order is immediately
placed in the local open-order snapshot, then reconciled against Robinhood.

The user also chooses a broker session and compatible order route. Regular hours can use market GFD
or whole-share limits. Extended and 24 Hour Market routes are whole-share limit-only, and overnight
eligibility is rechecked before each submission. Those fields are evidence-fingerprinted and
session-grant-bound. See [Trading sessions and order routes](TRADING_SESSIONS.md).

### Choosing a profile

- **Default retail low latency:** 1 s quotes, 5 s analysis bars, one pair decision per 3 bars, 5 s reconciliation.
- **Fast shadow experiment:** 0.25-0.5 s quotes and 1-5 s bars. Measure quote timestamps, duplicate
  snapshots, provider errors, spread, modeled slippage, and CPU usage. No real orders.
- **Live review:** only the exact cadence that passed Evidence Lab under realistic costs and has a
  current certificate. A cadence change resets warm-up and changes the evidence fingerprint.

Start faster settings in live shadow for several complete sessions. Promote nothing based on local
throughput alone; the gates require out-of-sample economics after costs. Current packaged research
has no passing live strategy certificate.

### Current provider boundaries

Robinhood's [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
describes portfolio/account reads, quotes, order review, and order placement through Trading MCP, and
warns that automated strategies can move quickly and be difficult to stop. Robinhood's
[market-data explanation](https://robinhood.com/us/en/support/articles/using-market-data/) distinguishes
displayed market prices from consolidated quotes and notes session-specific delays and extended-hours
risks. GRANDE Alpha therefore treats quote timestamps, age, spread, and broker review as controls—not
as proof of direct-feed quality.
