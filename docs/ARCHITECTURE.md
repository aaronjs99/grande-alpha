# Architecture

GRANDE Alpha keeps trading decisions, broker access, durable records, and presentation separate.
The Python package lives in `scripts/` and is installed as `grande_alpha`. `grande.ps1` is a
development launcher; the command line and desktop entrypoints are `grande-alpha-cli` and
`grande-alpha`.

## Current components

| Responsibility | Main modules | Boundary |
|---|---|---|
| Broker transport | `broker/robinhood_mcp.py`, `broker_permissions.py` | One MCP session owner; typed account, quote, review, order, and cancellation operations |
| Market and earnings inputs | `live_data.py`, `earnings_feed.py`, `market_calendar.py` | Fresh broker observations and timestamped earnings facts; missing facts do not become inferred trades |
| Strategy and replay | `strategy.py`, `earnings.py`, `sandbox.py`, `evidence.py` | Deterministic signals and simulation; research results do not authorize orders |
| Mixed execution | `mixed_engine.py`, `equity_execution.py`, `equity_ledger.py` | Exact-scope permit, limits, one durable reference per intent, fill reconciliation, and stop state |
| Persistence | `storage.py`, `agent_ledger.py`, `authorization.py` | Local SQLite records and credential-store-backed approval; unresolved records survive restart |
| Interfaces | `cli.py`, `autonomous_cli.py`, `controller.py`, `ui/` | The CLI runs the mixed engine; the desktop still runs its older attended ETF controller |

The desktop and mixed runner do **not** yet share a background worker. The controller is still
large, and persistence has not been divided into repositories. Those are remaining refactors,
not properties of the current release. Headless code must not import Qt; the desktop dependency
set is optional.

## Order lifecycle

1. Reconcile the exact broker account, positions, orders, and executable quotes.
2. Build a deterministic decision. A stock with missing point-in-time earnings data stays in
   cash; missing stock allocation is not shifted to leveraged ETFs.
3. Check the exact approved account, strategy revision, universe, order route, financial limits,
   data freshness, broker permissions, market window, and daily loss state independently.
4. Record an order intent and stable reference before any external placement call.
5. Review and submit only through the connected broker's supported contract.
6. Reconcile order and fill truth. An uncertain placement remains unresolved; it is never
   blindly retried. Repeated fills are deduplicated by broker identity.

Ordinary Stop or Revoke blocks new automatic submissions. A loss stop blocks new entries while
allowing an already-authorized, inventory-backed risk-reducing exit. Neither Stop nor window
close implies broker cancellation or liquidation. A cancellation is a separate, exact-scope
operation; open orders and positions must be checked at the broker after an outage.

## Recovery and ownership

The mixed engine uses a process lease, immutable account/order references, and an exact-scope
permit. A process restart must reacquire the lease and reconcile unresolved orders before it can
act. Approval does not expand when a strategy or limit changes. Loss recovery deadlines are stored
separately from the daily loss latch, so restarting cannot shorten a delay or erase that day's
loss. Alpha Vantage requests are reserved in a local rolling daily budget before a network call.

Application data belongs to the current user and must not be deleted during upgrades. Broker
OAuth and data-provider keys belong in the operating-system credential store, not the repository.
Configuration uses named saved sections and an explicit backed-up upgrade. The planned
authenticated, current-user-only worker and backed-up database migration are not yet implemented.
See the [capability matrix](USER_GUIDE.md#what-is-implemented-tested-and-still-pending)
for the current state and [development and release](DEVELOPMENT_RELEASE.md) for verification.
