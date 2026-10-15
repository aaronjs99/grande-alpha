# GRANDE Alpha

[![CI](https://github.com/aaronjs99/grande-alpha/actions/workflows/ci.yml/badge.svg)](https://github.com/aaronjs99/grande-alpha/actions/workflows/ci.yml)
[![Python 3.11–3.12](https://img.shields.io/badge/python-3.11–3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

GRANDE Alpha is a local-first Windows desktop and command-line project for trading research,
replay, and controlled broker execution. Its mixed stock/ETF candidate
uses earnings observations and a bounded QQQ/TQQQ/SQQQ allocation. Research works without a
broker account; external connections and real-order capabilities require separate opt-in.

An optional research-only MCP connection can run equity and crypto research inside the local
worker. Its tools cannot approve trading or place orders. See the
[research guide](docs/RESEARCH.md#agent-research-prompts-and-local-mcp).

> [!WARNING]
> This is experimental software, not investment advice. No included strategy is demonstrated to
> be profitable. Leveraged and inverse ETFs can lose substantial value, including all capital
> committed. Source checks do not establish provider-observed live readiness.

## Install and first use

On Windows 10 or 11 with Python 3.11 or 3.12, from the repository root:

```powershell
.\grande.ps1 setup
.\grande.ps1 run
```

The launcher uses the selected system Python; a persistent virtual environment is not required.
For headless use, install the core package without desktop extras and run `grande-alpha-cli --help`.
The package source lives under `scripts/`.

The desktop opens Antonio's **Agent desk**: virtual equity, paper P&L, recent activity, and six
research modules. **Start paper** opens editable virtual amounts and a synthetic demo; opening
the app starts nothing. AI is optional and off by default; paper and broker sessions work without
Ollama. **AI chat** can use an installed local model for read-only discussion after a RAM check.
Setup and technical details live in separate dialogs. **STOP** stays visible while scrolling.

**Research → Paper journal** opens saved virtual fills without a worker, broker or AI connection.
Choose a saved session, page through its fills, or export the displayed page to CSV. Saved marks
can be stale; synthetic and forward paper records do not establish profitability.

**Research → Compare strategies** compares a fixed momentum/breakout shortlist against cash on
local historical data, with identical virtual sizing and 1×/2×/3× execution costs. Selection uses
only development dates, then tests the chosen rule on later dates. It can reject every active
strategy. See [the comparison workflow and mathematics](docs/RESEARCH.md#strategy-comparison).

For broker execution, open **Session → Broker session setup** and follow
**Connect account → Set limits → Review → Start**. **New session** prepares a local candidate;
its visible example uses $10 per order, $100 exposure and a $5 daily loss stop for one hour.
Complete the account and symbol fields, adjust the settings, save, then Review. Incomplete files
can be saved and reopened as drafts but cannot be reviewed or started. Each user chooses
their own capital, symbols, order and exposure caps, daily loss limit, and recovery policy. An
approval may be made outside market hours, but an order still needs a supported open session,
fresh executable data, broker permission, and intact risk controls. Practice and replay are
optional research tools, not mandatory waiting periods or profitability certificates.
**Edit setup** is the single editor for a candidate and saves a new copy; the setup panel shows
its exact saved limits. Saving a changed copy requires a new review.

After building, `.\grande.ps1 install -Packaged` installs desktop and Start Menu shortcuts to the
local executable without updating shared Python packages. Source shortcuts launch Python directly;
neither shortcut uses an execution-policy bypass.

## Current limits

The desktop and command line control the same user-started hidden mixed stock/ETF worker. The
former attended ETF and live-shadow routes have been retired; historical records and offline replay
remain available. A focused offline worker-recovery validator is available; installed Windows and
provider-observed recovery are still pending. Verify the exact worker state rather than assuming a
desktop badge proves a broker session is live.
Provider-specific order schemas, earnings coverage, restart/partial-fill behavior, and installed
Windows operation still require end-to-end acceptance before this project can claim public live
autonomous readiness. There is no Windows scheduler, signed public installer, billing system, or
paid Pro activation in this checkout. Community features are local and free.

Stop blocks new local submissions; it does not automatically cancel broker orders or sell filled
positions. Disconnect and exit preserve unresolved order records. Always verify uncertain orders
and holdings at the broker. Credentials belong in the operating-system credential store, never
in repository files, screenshots, or issues.

The [capability matrix](docs/USER_GUIDE.md#implemented-behavior-and-remaining-work) separates
source implementation from remaining live work. See the
[user guide](docs/USER_GUIDE.md), [configuration and CLI](docs/CONFIGURATION_CLI.md),
[architecture](docs/ARCHITECTURE.md), [development and release](docs/DEVELOPMENT_RELEASE.md),
and [research](docs/RESEARCH.md). Dated results remain under
[historical records](docs/historical/README.md).

GRANDE Alpha is independent software, not affiliated with or endorsed by Robinhood, Alpha
Vantage, ProShares, Nasdaq, any broker, exchange, or fund sponsor. See [security](SECURITY.md),
[privacy](PRIVACY.md), [contributing](CONTRIBUTING.md), and [license](LICENSE).
