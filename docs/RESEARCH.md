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

## Agent research prompts and local MCP

The optional worker-hosted research service has concurrent, bounded **Equities** and **Crypto**
workers. They discover candidates, read quotes, and check data quality; they cannot submit broker
orders. Its virtual portfolio is a separate SQLite journal. Research settings and briefs can be
changed through the local MCP tools below. AI commentary does not change the live mixed strategy
or approve financial limits. Do not include secrets in briefs or research receipts.

MCP (Model Context Protocol) is an optional local stdio connection for a compatible AI client.
It is not a hosted URL and does not install a model, create a cloud account, or connect to every
AI product. Install the package with `.\grande.ps1 setup`, connect and review the account in the
desktop, then expand **Research MCP** and enable it. The equivalent CLI actions are
`grande-alpha-cli research mcp enable` and `grande-alpha-cli research mcp status`; status shows
the local bridge path. Configure a compatible client to launch the installed
`grande-alpha-mcp` stdio entrypoint with that bridge path. Reload the client's connection if
required. The standalone desktop build does not provide an independent Python runtime to an
external client; use a source installation for this feature.

The server exposes only:

| Tool | Research effect |
|---|---|
| `get_research_context` | Read worker progress, prompts, universe, timestamps, numeric observations, and proposal labels |
| `set_research_brief` | Change team, equity, or crypto brief for the next cycle |
| `configure_research_universe` | While stopped, set research symbols; an empty crypto list discovers supported USD pairs |
| `start_research` / `stop_research` | Start or stop both research workers through the local worker |
| `start_paper_trading` | Start a virtual-only paper session with explicitly supplied virtual cash and optional research inputs |

The `review_markets` prompt helps an AI client discuss observations and uncertainty. No MCP tool
places, reviews, cancels, or modifies an order; changes a cash limit; grants live authority;
accesses credentials; or runs arbitrary shell/file commands. The context excludes broker account
IDs, real balances, real positions, orders, provider errors, and free-form model reasons. It may
include observed quotes and the separate virtual portfolio. A client's provider may process
research data it receives; inspect that client's settings before enabling.

Access starts off on every worker launch. Disable revokes future access; Stop trading, Revoke,
and worker shutdown also revoke the bridge. Each enable gets a new session; the local SQLite
mailbox has a bounded queue, short lease, and expiring requests. A stopped worker cannot service
them. A client timeout leaves the research-command outcome uncertain, so inspect worker status
before retrying. Revocation cannot
retract data already delivered to a client. Local same-user software is outside the mailbox's
security boundary. There is no network listener or persistent MCP authorization.

### Virtual paper sessions

The desktop's optional research section starts a paper session without requiring an external AI
client. `start_paper_trading` offers the same operation through MCP. Both require explicitly
supplied `initial_cash` and `trade_cash`; no financial amount is prefilled. The default source is
`demo`, which uses fixed synthetic quotes and never calls market-data or model providers. Select
`source="broker_quotes"` only to simulate against current broker quotes. Optional public news,
public Bluesky search and a named local Ollama model are available for broker-quote sessions;
the demo rejects these options. Social results are unverified, and Ollama receives only research
observations and configured source excerpts. No X API or paid social connector is included.

The simulation cannot reach order-placement methods. `stop_research` or **Stop paper** stops the
simulation and discards pending virtual intents; existing virtual fills and positions remain in
the local paper journal. Reopening GRANDE shows the last journal but does not resume it.

The initial policy is `adaptive-trend-v1`: it requires distinct-quote warm-up, a rising fast trend,
a breakout, and estimated movement above spread, slippage and observed-noise costs. Paper exits
include modeled loss, trailing, target, trend-reversal and time rules. These are not broker-held
stops. Fills require a later valid quote and apply 5 basis points of adverse slippage. The model
omits commissions, market impact, partial fills and settlement constraints, so it can overstate
execution quality. P&L is not evidence of a profitable live strategy.

Paper research is stopped before a live mixed session starts and cannot run alongside that session.
News and public Bluesky context are optional, bounded inputs. Social material is unverified and
never establishes a fact. External prompts and source text are untrusted content, not instructions
to change strategy, scope or authority.

The MCP and paper journal are software interfaces, not deployment acceptance. Provider login,
current market-data permissions, the external MCP client's handling of data, and real-order
behavior require separate evidence.

## Historical findings

Dated reports and their images remain in [historical records](historical/README.md). In
particular, the [August baseline](historical/BASELINE_VALIDATION_2026-08-09.md),
[champion selection](historical/CHAMPION_SELECTION_2026-08-11.md), and
[strategy research](historical/STRATEGY_RESEARCH_2026-08-09.md) describe earlier versions and
cannot be treated as current performance claims. See the
[capability matrix](USER_GUIDE.md#implemented-behavior-and-remaining-work) for the current source
status and remaining acceptance work.
