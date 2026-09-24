# Agent paper trading

On **Agent · Stocks + Crypto**, click **Start offline demo**. Open **Session setup**
to change the price source, virtual cash or repeat option. Defaults are $1,000
virtual cash and $100 per buy. Select Robinhood quotes to **Start paper trading**.
Each session has its own portfolio; the cash never comes from Robinhood.

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
  only HOLD decisions. ChatGPT briefs do not replace the rules; optional local
  Ollama analysis works as before.

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
off when stopped. Research-only analysis remains available below the dashboard.
The page does not have token-launch, mempool, wallet, or social-media scanners.
Connecting ChatGPT through MCP does not turn these stages into autonomous LLMs.

Below the cards, the details show virtual cash, realized/unrealized/total P&L,
open virtual holdings, pending count, and the latest 30 simulated fills.
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
