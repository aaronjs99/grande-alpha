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

The optional worker-hosted research desk has concurrent, bounded **Equities** and **Crypto**
workers. They discover candidates, read quotes, and check data quality; they do not submit
orders. A failure in one market does not erase the other market's completed results. The older
Agent desktop page and its prompt editor remain in source for migration/reference but are not
opened by the current desktop entrypoint. Research settings and briefs can instead be changed
through the local MCP tools below. AI commentary does not change the deterministic trading
policy or approve financial limits. Do not include secrets in briefs or research receipts.

MCP (Model Context Protocol) is an optional local stdio connection for a compatible AI client.
It is not a hosted URL and does not install a model, create a cloud account, or connect to every
AI product. From this Windows source checkout, install the package with `.\grande.ps1 setup`,
connect and review the account in the desktop, then expand **Research MCP** and enable it. The
equivalent CLI action is `research mcp enable`; `research mcp status` shows the local bridge path.
Configure a compatible client to launch the installed `grande-alpha-mcp` stdio entrypoint with
that bridge path. Reload its connection if required. The standalone desktop build does not
furnish an independent Python runtime to an external client; use a source installation for this feature.

The server exposes only:

| Tool | Research effect |
|---|---|
| `get_research_context` | Read worker progress, prompts, universe, timestamps, numeric observations, and proposal labels |
| `set_research_brief` | Change team, equity, or crypto brief for the next cycle |
| `configure_research_universe` | While stopped, set research symbols; an empty crypto list discovers supported USD pairs |
| `start_research` / `stop_research` | Start or stop both research workers through the local worker |

The `review_markets` prompt helps an AI client discuss observations and uncertainty. No MCP tool
places, reviews, cancels, or modifies an order; changes a cash limit; grants live authority;
accesses credentials; or runs arbitrary shell/file commands. The context excludes account IDs,
balances, positions, orders, provider errors, and free-form model reasons. A client's provider
may process the research data it receives; inspect that client's settings before enabling.

Access starts off on every worker launch. Disable revokes future access; Stop trading, Revoke,
and worker shutdown also revoke the bridge. Each enable gets a new session; the local SQLite
mailbox has a bounded queue, short lease, and expiring requests. A stopped worker cannot service
them. A client timeout leaves the research-command outcome uncertain, so inspect worker status
before retrying. Revocation cannot
retract data already delivered to a client. Local same-user software is outside the mailbox's
security boundary. There is no network listener or persistent MCP authorization.

Local fake-broker, simulated-clock, and MCP protocol tests validate these software boundaries.
They do not exercise provider login, a live model, a real order, or a chosen AI client's setup.

## Historical findings

Dated reports and their images remain in [historical records](historical/README.md). In
particular, the [August baseline](historical/BASELINE_VALIDATION_2026-08-09.md),
[champion selection](historical/CHAMPION_SELECTION_2026-08-11.md), and
[strategy research](historical/STRATEGY_RESEARCH_2026-08-09.md) describe earlier versions and
cannot be treated as current performance claims. See the
[capability matrix](USER_GUIDE.md#what-is-implemented-tested-and-still-pending) for what the
current code actually implements and tests.
