# $100 background paper experiment

This is an independent, persistent **paper-only** worker for a $100 total budget
and a $10 total loss budget. It does not place broker orders or spend money on
hosting, news, AI or subscriptions. It uses your existing Robinhood quote access.
Wide crypto spreads can still block every entry; this change does not
establish a profitable strategy or remove those checks.

## Start from the app

1. Update `tony-dev`, then install its entry points with
   `.venv/bin/python -m pip install -e .` on macOS.
2. Open GRANDE, enable broker access in Settings, and connect Robinhood using the
   existing sign-in flow. Stop the old desktop paper session to avoid confusing its
   separate results with this experiment.
3. In **Agent**, find **$100 background paper experiment** and click
   **Start background paper**. The profit dashboard opens in your browser when ready.
4. You may close the browser and the desktop app. Keep the computer powered, awake
   and connected. The worker reads saved sign-in credentials from the same OS
   keychain. If sign-in expires, reconnect through GRANDE; the worker does not open
   authentication windows unattended. Disabling broker access in Settings stops
   its market-data reads.

Use **Open profit dashboard** to return. **Stop background paper** or the dashboard's
stop control stops this service. The original desktop **Stop agent** controls its
own session, not the separate background service. Stopping the background worker
saves paper holdings rather than inventing exit fills. Start resumes the same
experiment. Pending pre-interruption intents are discarded, quote histories warm
up again, and holdings need fresh valuations before new entries.

## What is measured

- $100 initial virtual cash; $10 per proposed buy; at most two positions and 20%
  initial-capital exposure under the existing adaptive price strategy.
- A focused paper universe of SPY, QQQ, BTC-USD and ETH-USD. Equities retain the
  existing regular-session checks. Crypto retains broker pair eligibility checks.
- Actual quoted spread, 5 basis points adverse slippage per side, and **additional
  fee stress assumptions** of 1 basis point (0.01%) for equity and 25 basis points
  (0.25%) for crypto, per side. These are explicitly labeled assumptions, not
  verified fees for your Robinhood routing. Some routing charges can already be
  embedded in a quote; an extra fee allowance may therefore overestimate costs.
- Fee-inclusive buy sizing; sale fees; estimated exit fees and slippage in open
  position values. Fees paid are displayed separately but never deducted twice.
- Equity proceeds are unavailable for new buys until the next scheduled market
  session opens, using the existing holiday calendar. Crypto proceeds are modeled
  as immediately available. This is a simulation, not broker buying power.
- Manually recorded operating expenses, including electricity estimates. They
  reduce virtual cash and the all-in experiment return. Nothing is purchased.
  Retrying the same expense receipt does not count it twice. Expenses are permanent
  records in this first version; check the amount before submitting.

**Net profit = cash + unsettled proceeds + estimated liquidation value of open
holdings − $100.** Operating expenses have already reduced cash. Trading profit
adds operating expenses back to isolate the trading result. An old quote is labeled
as an old valuation, not presented as a current executable exit price.

At a $10 observed net loss, a durable loss lock cancels pending buys, blocks new
buys, and requests paper exits on eligible quotes. Gains later, midnight, restart,
or the Resume entries button cannot clear it. The strategy retains its existing
3% maximum observed drawdown entry pause, which can stop entries sooner. Limits
are triggers, not guaranteed loss caps; unavailable/wide quotes, gaps and outages
can delay exits. The simulator still omits depth, queue position, partial fills and
real broker minimum sizes, so paper results are not live performance evidence.

## Terminal controls

Run from your updated source checkout:

```bash
.venv/bin/python -m grande_alpha.paper_worker start
.venv/bin/python -m grande_alpha.paper_worker dashboard
.venv/bin/python -m grande_alpha.paper_worker status
.venv/bin/python -m grande_alpha.paper_worker stop
```

The dashboard is local to this computer: `http://127.0.0.1:8767`. It has no public
listener or remote access. Settings and holdings remain in a separate
`paper-experiment` directory within GRANDE's existing OS app-data directory.
The desktop paper ledger is not migrated or overwritten. There is no reset control
that silently replenishes the experiment's money or loss budget.

## Automatic recovery and macOS login

Start launches a detached supervisor. It restarts a crashed child worker, uses a
heartbeat to detect a stuck child, and uses OS locks to prevent duplicate writers.
The worker retries failed saved sign-in and data connections. A corrupt database or
occupied dashboard port stops startup for review instead of replacing saved data.

To resume at future macOS logins using the saved running/stopped choice:

```bash
.venv/bin/python -m grande_alpha.paper_worker install-login
```

This writes only `~/Library/LaunchAgents/com.grande-alpha.paper.plist`; it takes effect
at your next login. It needs the same Python installation and OS keychain. Stop
persists across login, so a deliberately stopped experiment stays stopped.
Remove that login entry with:

```bash
.venv/bin/python -m grande_alpha.paper_worker remove-login
```

This is local hosting: sleep, power loss, shutdown and internet loss interrupt data
collection. No catch-up trades or invented fills occur after an interruption.

## Validation

Tests cover fee/cash conservation, estimated exit costs, expense idempotency,
equity settlement over holidays, loss locks, paused entries with working exits,
stale positions, saved-session recovery, account changes, broker read restrictions,
local HTTP controls and permission revocation. The existing adaptive strategy,
paper and desktop tests remain relevant. Tests use synthetic data and fake brokers;
no real orders or funded accounts are involved.
