# Agent paper trading

On **Agent · Stocks + Crypto**, use **Paper trading · virtual money** and click
**Start new paper session**. Defaults are $1,000 virtual cash and $100 per buy.
Each session has its own portfolio; the cash never comes from Robinhood.

## Two price sources

- **Offline demo** runs 24 accelerated cycles in about 24 seconds. Both market
  workers use clearly labeled `DEMO-STOCK` and `DEMO-USD` instruments with fixed,
  made-up prices. It needs no broker connection and calls no broker or AI provider.
  The normal rules and data checks generate the signals. The path intentionally
  exercises buys and sells; its P&L is not evidence of strategy performance.
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

The panel shows virtual cash, portfolio value, realized/unrealized/total P&L,
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
automatically after its final cycle. Reopening the app displays the last paper
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
- `get_research_context` includes a `paper` report and `observation_source`.
  `synthetic_demo` observations must never be described as real market prices.
- `stop_research` stops research or paper simulation while keeping MCP enabled.

These tools cannot place broker orders or change real-money limits. Research
access shares virtual balances, simulated holdings, fills and P&L with the
connected AI client; real balances, credentials, positions and orders remain
excluded. Scheduled ETF shadow cannot start this feature.
