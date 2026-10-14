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
holdout periods; report failures as well as passes. Paper simulation measures software behavior and
operational handling, but cannot prove future profit or exact live fill prices.

Legacy Evidence Lab certificates and exact-runtime manifests are retained as historical research
records. They are not a mandatory profit gate for the mixed live route. Independent technical
checks for broker permission, exact approval, risk limits, durable recovery, and executable data
remain necessary. Crypto Agent Desk material remains exploratory; it is not part of the mixed
stock/ETF execution route.

## Strategy comparison

Open **Research → Compare strategies**, choose a local historical JSON cache, then **Compare**.
Input requires aligned QQQ/TQQQ/SQQQ OHLCV bars, a matching content hash, regular calendar
sessions, and at least 30 trading days at 1m or 5m cadence. Outside-session and off-grid bars
are rejected. Empty, all-zero-volume closing stamps in old chart caches are excluded explicitly;
the report records original and normalized hashes and the excluded count. Source files are
preserved. Other missing volume remains a limitation. The headless equivalent prints the same report:

```powershell
grande-alpha-cli research strategies compare --dataset path\to\historical-cache.json
```

The fixed shortlist contains cash, existing EMA momentum, multi-horizon trend, opening-range
breakout, and experimental `cost_aware_trend`. There is no parameter optimization. All use the
same virtual money, costs, caps, settlement, exits and timing. Illustrative defaults are $1,000
starting cash, at most $100 per entry, 40% exposure, 0.5% stop-risk budget, 2% daily loss pause,
and a 15% annualized volatility target. These are research assumptions, not live limits.
QQQ signals route to virtual TQQQ/SQQQ: this is concentrated leveraged ETF research. The
quote-led paper worker and live earnings/mixed-allocation route remain separate. A comparison
never switches or starts either route.

The first 70% of sessions are development, followed by one purged session and the final block.
Development contains three disjoint chronological blocks. A rule needs positive after-cost
returns in all three blocks and at 1×/2×/3× development costs, at least 30 fully closed trades,
drawdown at most 3%, and no residual holdings or special session-close liquidation fills.
Among survivors, select highest worst-block return, then lower drawdown, then total return.
Only that candidate and cash are opened on final dates. A failed candidate is not replaced
after seeing the final outcome. Final checks require positive return, at most 3% drawdown,
at least 10 closed trades and no residual/close-attempt dependency at every cost level.
These thresholds are screening choices, not confidence levels or guarantees.

The plan hash binds dates, data, all configurations and rules. Repeated comparisons reuse the
same final dates: this is **not** a sealed holdout or profitability certificate. The full
Evidence Lab has stricter provenance/trial/holdout checks for historical ETF research; those
certificates do not approve the mixed live route either. The comparison always reports
`live_approved=false` and cash as the operational fallback. The research candidate and its
final pass/fail are reported separately.

For the experimental rule, `h` is a horizon in completed bars (3, 12 or 36), `P` is observed
QQQ close, and `r_h` is its cumulative log movement in basis points:
`r_h = 10000 * log(P_now / P_h_bars_ago)`. The noise estimate `s` is the population standard
deviation of the last 36 one-bar log movements in basis points. The fixed cost floor `c` is
modeled spread plus twice per-side slippage and round-trip commission divided by entry notional,
in basis points. The hurdle `t_h` is `max(trend_threshold_bps, c + 1.5 * s * sqrt(h))`.
Every horizon must clear its hurdle in the same direction; otherwise the signal is flat.
This filters past movement, not a forecast of profitable future movement. The noise multiplier
is fixed and not statistically calibrated. Indicators and sizing volatility rewarm each
session in this comparison. Fifteen-minute and daily bars cannot support this intraday warm-up
consistently and are excluded; existing generic daily research retains cross-session indicators.

Existing sizing caps entries by requested notional, equity times the risk budget divided by
stop distance, and the exposure limit. It then scales down when realized volatility exceeds
its target and respects settled cash and fees. Gaps, delayed exits and execution failures can
cause larger losses than the nominal risk budget. Cost stress freezes signal hurdles and changes
execution costs only.

Generic replay executes scheduled opening actions before evaluating the current completed close.
A cash exit completes before new direction changes; daily pauses do not keep moving its deadline.
A losing rotation updates loss admission before a replacement buy. Partial sells count as one
trade only upon full closure. Calendar-close attempts obey configured execution constraints;
the end of a truncated recording is not the session close. Residual holdings stay valued and
fail the screen. OHLCV cannot establish executable historical quotes, actual latency, tax or fills.

The rationale draws on [time-series momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)
and [volatility-managed portfolios](https://www.nber.org/papers/w22208). Their instruments,
horizons and implementations differ; they do not validate these intraday rules. Small fixed
searches and disclosure of repeated tests address the concern described in
[The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

## Agent research prompts and local MCP

The optional worker-hosted research service has concurrent, bounded **Equities** and **Crypto**
workers. They discover candidates, read quotes, and check data quality; they cannot submit broker
orders. Its virtual portfolio is a separate SQLite journal. Research settings and briefs can be
changed through the local MCP tools below. AI commentary does not change the live mixed strategy
or approve financial limits. Do not include secrets in briefs or research receipts.

MCP (Model Context Protocol) is an optional local stdio connection for a compatible AI client.
It is not a hosted URL and does not install a model, create a cloud account, or connect to every
AI product. Install the package with `.\grande.ps1 setup`, connect and review the account in the
desktop, then open **Session details** and enable **research MCP**. The equivalent CLI actions are
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

The desktop's **Start paper** dialog starts a paper session without requiring an external AI
client. `start_paper_trading` offers the same operation through MCP. Both require explicitly
supplied `initial_cash` and `trade_cash`. The desktop offers editable virtual defaults of $1,000
and $100; these are not live financial limits. The default source is
`demo`, which uses fixed synthetic quotes and never calls market-data or model providers. Select
`source="broker_quotes"` only to simulate against current broker quotes. Optional public news,
public Bluesky search and a named local Ollama model are available for broker-quote sessions;
the demo rejects these options. Social results are unverified, and Ollama receives only research
observations and configured source excerpts. No X API or paid social connector is included.

The simulation cannot reach order-placement methods. `stop_research` or **Stop paper** stops the
simulation and discards pending virtual intents; existing virtual fills and positions remain in
the local paper journal. Reopening the desktop reconnects to an existing worker's reported
paper journal; a fresh worker does not resume paper automatically.

The demo uses the existing legacy rules. A fresh broker-quote paper worker defaults to
`adaptive-trend-v1`, retaining saved worker settings: it requires distinct-quote warm-up, a rising fast trend,
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

### Local desk chat

**AI chat** restores Antonio's plain-text conversation view around the shared worker. An explicit
Send uses `http://127.0.0.1:11434/api/chat`, with redirects and environment proxies disabled.
It sends only bounded paper metrics, six module stages, recent research events and up to four
conversation messages. Broker account identifiers, real balances/positions/orders, credentials,
source documents, prompts and local paths are excluded. Replies are advisory; the format rejects
tool calls and setting changes. The chat has no worker-control callback. No model is installed
or contacted merely by opening the dialog. Model availability and answer accuracy are separate
from the offline transport checks.

## Historical findings

Dated reports and their images remain in [historical records](historical/README.md). In
particular, the [August baseline](historical/BASELINE_VALIDATION_2026-08-09.md),
[champion selection](historical/CHAMPION_SELECTION_2026-08-11.md), and
[strategy research](historical/STRATEGY_RESEARCH_2026-08-09.md) describe earlier versions and
cannot be treated as current performance claims. See the
[capability matrix](USER_GUIDE.md#implemented-behavior-and-remaining-work) for the current source
status and remaining acceptance work.
