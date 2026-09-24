# Architecture

GRANDE Alpha keeps trading decisions, broker access, durable records, and presentation separate.
The Python package lives in `scripts/` and is installed as `grande_alpha`. `grande.ps1` is a
development launcher; the command line and desktop entrypoints are `grande-alpha-cli` and
`grande-alpha`.

## Current components

| Responsibility | Main modules | Boundary |
|---|---|---|
| Broker transport | `broker/robinhood_mcp.py`, `broker/permissions.py` | One MCP session owner; typed account, quote, review, order, and cancellation operations |
| Market and earnings inputs | `data/live_data.py`, `data/earnings_feed.py`, `domain/market_calendar.py` | Fresh broker observations and timestamped earnings facts; missing facts do not become inferred trades |
| Strategy and replay | `strategy/`, `data/earnings.py`, `research/` | Deterministic signals and simulation; research results do not authorize orders |
| Mixed execution | `execution/mixed_engine.py`, `execution/equity_execution.py`, `execution/equity_ledger.py` | Exact-scope permit, limits, one durable reference per intent, fill reconciliation, and stop state |
| Persistence | `persistence/`, `execution/authorization.py` | Local SQLite records and credential-store-backed approval; unresolved records survive restart |
| Research agent | `research/agent_runtime.py`, `research/agent_analyst.py`, `research/agent_bridge.py`, `research/agent_mcp.py` | Concurrent equity/crypto research, optional local AI, and a separate per-session MCP mailbox; no broker-write tools |
| Local worker controls | `execution/session_worker.py`, `execution/worker_control.py`, `execution/worker_ipc.py`, `execution/worker_process.py` | User-started hidden mixed worker and authenticated local controls; tested with substitutes, not a live-deployment certificate |
| Interfaces | `cli.py`, `interfaces/cli/`, `app.py`, `ui/session_window.py` | Desktop and mixed CLI control one worker; attended/shadow CLI routes remain foreground |

The desktop and autonomous mixed CLI use one user-owned hidden worker; attended and shadow CLI
modes remain foreground paths. Worker, IPC, and persistence seams are tested locally with
substitutes but are not an installed, provider-validated shared trading service. In particular,
the research MCP and the broker's trading MCP are separate protocols and permissions.
The research bridge cannot inherit a trading grant, and its prompts and proposals cannot reach
an order path. Headless code must not import Qt; the desktop dependency set is optional.

The research MCP starts disabled each worker session. An explicit desktop or CLI opt-in creates a
new local mailbox lease; a compatible AI client starts a stdio server and can exchange only bounded
research commands with the worker. Stop, Revoke, or shutdown revokes the lease and discards queued
work; expired commands cannot become later broker actions.
The bridge has no public listener, account export, or broker credentials. See
[research and setup](RESEARCH.md#agent-research-prompts-and-local-mcp).

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
Configuration uses named saved sections and an explicit backed-up upgrade. Worker control and
execution-store migration code need targeted integration and installed-Windows acceptance before
they can be treated as operational guarantees. The execution-store upgrade is explicit and offline;
startup does not silently replace historical journals.
See the [capability matrix](USER_GUIDE.md#what-is-implemented-tested-and-still-pending)
for the current state and [development and release](DEVELOPMENT_RELEASE.md) for verification.
