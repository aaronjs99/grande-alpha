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

The intended user flow is **Connect account → Set limits → Review → Start**. Each user chooses
their own capital, symbols, order and exposure caps, daily loss limit, and recovery policy. An
approval may be made outside market hours, but an order still needs a supported open session,
fresh executable data, broker permission, and intact risk controls. Practice and replay are
optional research tools, not mandatory waiting periods or profitability certificates.

## Current limits

The desktop and command line control the same user-started hidden mixed stock/ETF worker. The
former attended ETF and live-shadow routes have been retired; historical records and offline replay
remain available. The automated regression suite has been removed; installed Windows and
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
