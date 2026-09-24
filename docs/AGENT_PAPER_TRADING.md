# Agent paper trading

New desktop sessions select **Adaptive trend · paper experiment** in **Session setup**.
It makes price decisions each quote with independent exits and exposure limits;
Qwen supplies optional advisory context, and missing news coverage does not prevent
entry. **Legacy** retains the earlier rules/AI decisions and two-publisher news filter.
See [the adaptive strategy, validation and limits](ADAPTIVE_PAPER_STRATEGY.md).

## Diagnose a run with zero fills

Below Start/Stop, **Why no trades?** expands the latest completed check. Its dated
stock/crypto summary remains visible while the next quote request is in progress.
**Copy trading diagnostics** copies a plain-text report you can paste for support.
The report includes bid/ask prices, actual spreads versus limits, quote age, warm-up
progress, price movement versus the rules threshold, news-filter and analyst status,
and virtual fill/pending/position counts. It excludes account identifiers, real
balances, credentials, research prompts and raw provider errors. No file is written
and copying does not start or change a run. Stop retains the last dated report; a new
run clears it until the first check completes. Reports cover only the sampled batch.

In **Legacy** mode, **Rules baseline** means continuous AI is off. A connected ChatGPT research chat does
not run the local analyst. The rules require movement greater than the larger of
0.20% and twice the current spread after warm-up; the crypto spread limit is 1.00%.
For example, a 0.80% spread passes that limit but requires a rise greater than 1.60%
over the observed window for a rules BUY. A run can therefore remain on HOLD even
with valid quotes. A smaller watchlist does not guarantee a trade. These diagnostics
do not loosen entry checks, create signals, or alter simulated-fill economics.

## Start paper trading

On **Agent · Stocks + Crypto**, connect Robinhood and click **Start continuous paper
trading**. The desktop now selects **Robinhood quotes** by default. Open **Session
setup** to change the price source or virtual cash; choose **Offline demo** explicitly
to run the fixed demonstration. Defaults are $1,000 virtual cash and $100 per buy.
Each session has its own portfolio; the cash never comes from Robinhood. Starting
paper trading creates virtual orders only, and nothing starts automatically on launch.

## Two price sources

- **Offline demo** runs 24 accelerated cycles in about 24 seconds. Both market
  workers use clearly labeled `DEMO-STOCK` and `DEMO-USD` instruments with fixed,
  made-up prices. It needs no broker connection and calls no broker or AI provider.
  The normal rules and data checks generate the signals. The path intentionally
  exercises buys and sells; its P&L is not evidence of strategy performance.
  **Repeat offline demo until I press Stop** repeats the fixed price path while
  advancing the synthetic clock through weekdays. This remains made-up data.
- **Robinhood quotes** uses the current watchlists, cadence and analysis settings.
  A connected broker is required for market data. Existing session, freshness,
  spread, pair restriction and warm-up checks remain in force. A run may produce
  only HOLD decisions. ChatGPT briefs do not replace the rules; the optional local
  Ollama analyst must be configured and enabled to analyze quotes and news with AI.

## Continuous monitoring

Robinhood paper sessions run until Stop, Disconnect, Exit, or an unrecoverable error;
they have no 24-update demo limit. Quote checks target **five seconds** by default,
configurable from 5–300 seconds in **Configure universe and AI**. Processing time
counts toward that interval instead of adding another full pause afterward. Missed
polls are skipped, not queued for a catch-up burst. All-market provider errors cause
backoff, reaching 60 seconds at the default cadence. This is bounded polling, not
a streaming or high-frequency execution service. Slow provider calls can exceed
the requested interval, and broker session calls remain serialized.

Stock and crypto reads run concurrently. Each market's eligible paper fills and
valuations are processed as soon as its observations arrive; a slow peer no longer
holds up those fills. Up to 20 instruments per market are checked per update.
Open virtual positions and pending intents receive priority, with remaining capacity
rotating through other candidates. Adaptive sessions keep each discovery group on
every poll for 120 seconds before rotating, to collect the recent observations needed
by the price strategy. Legacy sessions retain their earlier per-update rotation.
If more than 20 held/pending instruments exist
in one market, that priority group itself rotates. This does not scan every traded
asset at once, and unsupported or absent quotes cannot be invented.

In Legacy mode with local AI enabled, the next quote batch also prioritizes the instruments in
its outstanding analysis, after holdings and pending intents. A completed answer is
matched to fresh observations for those instruments before discovery moves on. After
consuming the answer, the scan rotates before starting another analysis. This fixes
AI answers being discarded because each update had already switched to a different
crypto batch. The batch limit and current-quote freshness checks still apply.

News collection runs in the background on its existing ten-minute cadence. Optional
AI analysis also runs in the background with at most one request per market in flight.
Fresh quote checks and valuations continue while it works. In **Legacy** mode, no
rules-based buys replace a pending or failed AI response. Each result is used once, only with eligible current
quotes, unchanged research settings and instrument identity, and still-available input
source IDs. Continuous paper analysis has a separate lifetime: a request may run for
45 seconds, and its newest input observation and request start must be no more than
60 seconds old when the reply is used. The **current** quote must still pass the
existing 15-second freshness limit, spread checks, pair restrictions and market hours.
The input observation must remain in the uninterrupted valid quote history, with a
newer quote now available. Any observed midpoint excursion, or current bid/ask change,
greater than 20 bps (0.20%) from the corresponding input price rejects the reply and
requires a fresh analysis. This checks observed updates; it cannot certify price moves
between updates. Source and news checks run again before any simulated entry.

For legacy model decisions, this separates inference latency from executable quote age: a 19–23-second reply no
longer expires solely because inference exceeded 15 seconds. Invalid, expired or
changed-market replies still produce HOLD. Acceptance queues a paper intent only;
a later eligible quote is still required for a fill. No rules-based buys replace a
failed model response in legacy mode. These are simulation controls, not validated real-order authority.

The live status shows current request elapsed time. **Last AI result** persists while
the next request runs and reports its model, completion time, request duration, usable
proposal counts or a sanitized timeout/connection/HTTP/format failure. Usable proposal
counts precede the news and paper-fill checks and are not trade counts. The report is
also available through **Copy trading diagnostics** and the research MCP context.

Ollama's response schema now names every requested instrument explicitly and limits
each instrument's citations to its supplied article IDs. With no articles, the
required citation list is empty; HOLD and EXIT do not require news citations. When
the enabled news filter already blocks new buys, that instrument's requested action
choices are HOLD and EXIT. This prevents asking the model to invent supporting news.
The parser independently checks every reply, including duplicate fields, instrument
identity and direct-news citations for news-backed buys. Valid replies in the earlier
array format remain supported. Invalid replies are not repaired into trading signals.

**Last AI result** distinguishes invalid JSON, missing decision fields, changed
instruments, invalid actions, unknown article IDs, missing direct-news citations,
incomplete replies and output-limit failures using fixed error codes. It never copies
raw model output into an error. These checks do not change the two-publisher news
requirement, spread limits, quote freshness, or the later-quote virtual fill rule.

The initial four-distinct-quotes / 60-second warm-up still applies. The history buffer
now retains enough samples at five-second cadence to satisfy it; the demo keeps its
original fixed observation window. Model latency, market hours, news coverage, spread
checks, virtual cash and strategy signals can all result in no fills. Continuous
monitoring does not mean constant buying and selling or establish profitability.

Both workers share one virtual cash balance. A buy signal queues an intent; it
can fill only on a later distinct eligible quote, within ten minutes of the
signal. Buy fills use ask plus 5 basis points (0.05%) slippage; sells use bid
minus the same slippage. Blocked observations discard pending intents for that
instrument. A symbol has at most one holding; repeated buys do not accumulate
positions. Exits sell the whole virtual holding. Cash is checked again at fill
time so simultaneous workers cannot overdraw it. No shorts or borrowing.

## Reading the results

The main dashboard shows virtual portfolio balance, total P&L and percentage
return, simulated fills, and win rate. Win rate is profitable closed virtual
trades divided by all closed virtual trades, including flat outcomes; open
positions and buy fills do not count. Before a paper session exists, the balance
card can show broker account value, labeled separately from paper results.

The equity chart follows the current paper session's last 500 cycle observations.
Prices and the activity timestamps use a synthetic clock in demo mode. Elapsed
time measures actual session duration. Quote refreshes cannot substitute the
real account's balance for virtual equity. The activity log includes actual
worker handoffs, data checks, proposals, simulated fills and portfolio updates.
It keeps the latest 200 rows and identifies saved fills after reopening.
The activity log displays **Pacific time**, using `America/Los_Angeles` and explicit
PST/PDT labels. Hover over a time for its full local date and UTC offset. This changes
display only: saved events, decision clocks and market-hour checks remain in UTC.
Demo activity remains labeled as a simulated clock.

The status directly below the session buttons explains whether the run started, is
fetching data, is collecting its initial quote history, or is waiting for the next
cycle. It also summarizes closed-session/stale-quote restrictions, missing news
coverage, market-provider errors, and signals that have no virtual holding to exit.
The next-quote-check countdown shows the scheduled pause; network processing can
take additional time. Background AI and news progress are displayed separately.
A running session can legitimately have zero fills. Startup errors appear beside the controls, and a disabled paper
button explains when a Robinhood connection is required.

HOLD details include the observed price change and rule threshold, or the model's
actual waiting/error reason. News-blocked buys show the number of matching news
publishers out of two. An AI veto is not mislabeled as missing news when coverage
passes or the news filter is disabled. These explanations do not lower entry checks
or force trades. See [X monitoring and research setup](AGENT_MARKET_RESEARCH.md).

While a quote update is in progress, its elapsed time keeps updating independently
of broker responses. Worker labels distinguish stock scanning, crypto-pair loading
and quote requests. After ten seconds waiting on broker data, the dashboard explains
the delay; an overdue update also gives Stop/reconnect instructions. A closed stock
session blocks stock fills, not the monitoring loop or crypto worker.

The broker's per-request deadline now covers transport sending as well as waiting
for a response. A blocked send can no longer bypass the ten-second read deadline
and hold later requests indefinitely. Timed-out requests are reported as unavailable;
subsequent monitoring polls may try reading again. Order writes are never automatically
resubmitted. An unexpectedly exited agent task clears its running state and pending
virtual intents and shows an interruption message; it does not restart itself.

Six named cards expose stages in the workflow, not six independent AI models:

| Card | Work |
| --- | --- |
| NOVA | Stock/ETF discovery and quotes |
| ORIN | Crypto discovery and quotes, concurrent with NOVA |
| VELA | Rules analysis or the configured local Ollama model |
| KADE | Quote, session, spread and eligibility checks |
| RUNE | Next-quote virtual fills |
| ZARA | Shared virtual portfolio and cycle summaries |

Agent comms and card highlights follow emitted runtime events; highlights turn
off when stopped. Card glows ease in and out, and active avatars gently breathe.
Brief handoffs remain visible before fading; ordinary refreshes do not restart
the motion. Animations pause on hidden pages without queuing old events or
changing the trading cadence. Research-only analysis remains available below the dashboard.
Optional news feeds and a bounded public Bluesky search are available in **Session setup**.
See [news research and performance evaluation](AGENT_MARKET_RESEARCH.md) for source coverage,
buy filters, provenance and provider limitations. There are no token-launch, mempool or wallet scanners.
Connecting ChatGPT through MCP does not turn these stages into autonomous LLMs.

Below the cards, the details show virtual cash, realized/unrealized/total P&L,
open virtual holdings, pending count, and the latest 30 simulated fills.
The evaluation line adds average closed-trade P&L, profit factor and maximum observed
drawdown. These describe recorded paper outcomes; they are not profitability certificates.
Holdings are valued at their last eligible bid, with quote timestamps and old
valuations labeled. A rotating discovery universe does not update every holding
every cycle. Portfolio value includes those last known marks.

The fill model uses fractional quantities rounded down to eight decimal places,
immediate settlement, no fees and no liquidity or partial-fill model. It is a
software simulation, not a forecast of executable prices or future returns.

**Stop agent**, **STOP + CANCEL**, Disconnect and Exit stop the worker runtime and
discard pending virtual intents. Completed fills and virtual holdings remain
visible; stopping does not force a simulated liquidation. The offline demo stops
automatically after its final cycle unless repeat is checked. Reopening the app displays the last paper
session but never resumes it. Starting again creates a new session and archives
the prior one; it does not reset any real trading budget, order or position.

Sessions and every simulated fill are saved in a separate `*-paper.db` SQLite
file beside the app's audit database. The live execution ledger is untouched.
The paper implementation has no broker write, account or authorization callbacks.

## ChatGPT / MCP

After updating GRANDE, restart its tunnel process and refresh the plugin's tool
discovery if the new tool is not listed. With session research access enabled:

- `start_paper_trading(source="demo", initial_cash=1000, trade_cash=100)` starts
  a new offline simulation while stopped. Use `source="broker_quotes"` for live
  market observations with virtual fills.
  `broker_quotes` is now the MCP tool's default when source is omitted.
  Optional `loop_demo=true` repeats only the offline demo until stopped.
- `get_research_context` includes a `paper` report and `observation_source`.
  `synthetic_demo` observations must never be described as real market prices.
  The paper report includes win/loss counts and equity history; `team_status`
  and `team_events` expose the same runtime handoffs shown on the dashboard.
- `stop_research` stops research or paper simulation while keeping MCP enabled.

These tools cannot place broker orders or change real-money limits. Research
access shares virtual balances, simulated holdings, fills and P&L with the
connected AI client; real balances, credentials, positions and orders remain
excluded. Scheduled ETF shadow cannot start this feature.
