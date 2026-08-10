# GRANDE Alpha

GRANDE Alpha is a local-first Windows desktop workstation for researching leveraged-ETF strategies. It starts in research mode, provides deterministic and historical replay for synthetic `TQQQS`/`SQQQS` instruments, and keeps broker access and real-order controls behind separate, revocable permissions.

> Community preview: this software is experimental, provides no investment advice, and cannot promise profit. Leveraged and inverse ETFs seek daily objectives; results over longer periods can differ materially. You can lose the entire amount traded.

GRANDE Alpha is independent software. It is not affiliated with, endorsed by, or sponsored by Robinhood Markets, Inc., ProShares, Nasdaq, or any broker, exchange, or fund sponsor.

## Public-safety defaults

- First launch opens a disclosure-led onboarding flow; research mode is the default.
- Broker access, remote community market data, the optional personal ledger, and real-order controls are independent opt-ins.
- Broker OAuth credentials are stored through the operating-system credential vault, never in project files.
- Enabling real-order controls requires a current passing Evidence Lab certificate for the exact strategy settings plus an explicit settings phrase. Every application launch still starts locked, and each live session needs a separate time-limited confirmation and numeric limits.
- The red stop control revokes local order authority and attempts to cancel open Agentic equity orders. It cannot guarantee cancellation during a network or provider failure and does not liquidate filled positions.
- A redacted diagnostic export is available for support. The application sends no first-party telemetry.

## Start here

On Windows, the immediately usable path is the signed-Python source launcher:

```powershell
.\setup.ps1
.\doctor.ps1 -Full
.\install-local.ps1
.\run.ps1
```

`build.ps1` writes an **unsigned release candidate** to `dist\GRANDEAlpha`; Windows Smart App Control or enterprise Code Integrity may block it until it is Authenticode-signed with a trusted publisher identity. See [Quickstart](docs/QUICKSTART.md), [Windows installation](docs/WINDOWS_INSTALLATION.md), [Safety](docs/SAFETY_AND_COMPLIANCE.md), [Privacy](PRIVACY.md), and the complete [documentation index](docs/README.md).

Source bundles place their managed Python environment at `%LOCALAPPDATA%\GRANDEAlpha\runtime` to
avoid PySide6 installation failures caused by deeply nested Windows extraction paths. An existing
developer checkout `.venv` remains preferred.

## What it is—and is not

This is not exchange-colocated high-frequency trading. It is a desktop research and consent-gated automation client with a retail low-latency profile: batched quotes target 1-second polling, account truth reconciles every 5 seconds, signals use completed 5-second analysis bars, and the default pair-action decision occurs every 3 bars (15 seconds). Slow provider calls are coalesced instead of queued. Observation speed does not increase the separately bounded live order rate. Its baseline strategy observes QQQ-derived signals and expresses each TQQQ/SQQQ/cash transition in the exact nine-action `(T,S)` command vocabulary. The included strategy is an engineering baseline, not a demonstrated edge. See [Low-latency execution](docs/LOW_LATENCY_EXECUTION.md).

The sandbox models costs, spread, latency, partial fills, volume limits, rejections, and purged walk-forward evaluation. It includes a finite, documented research library: EMA momentum, multi-horizon trend, a paper-faithful first-half-hour momentum rule, an older rest-of-day closing hypothesis, opening-range breakout, and a conservative agreement ensemble. The separate nine-action lab represents every `(T,S)` command pair where each leg is sell `-1`, hold `0`, or buy `+1`, trains an auditable offline policy, and evaluates it on a later chronological holdout. It also shows fixed and volatility-managed daily exposure benchmarks. These are hypotheses, not guaranteed edges. Evidence results are recorded locally with a unique candidate-trial ledger and Deflated Sharpe gate. Only a result that passes every current gate can unlock the separate live-review workflow for the same strategy fingerprint, bar interval, and tested risk envelope; Action Lab results cannot unlock trading. A certificate expires after 30 days and still does not predict future returns or itself authorize an order.

## Broker integration

The optional adapter uses Robinhood's official Trading MCP endpoint and browser OAuth. Robinhood states that a connected third-party agent can read data across Robinhood accounts, while trading is restricted to the dedicated Agentic account. The provider consent is broader than the app's read-only diagnostic: that diagnostic never invokes order review, placement, cancellation, or watchlist tools, but the granted OAuth scope can include those capabilities. Review the current provider disclosures before opting in: <https://robinhood.com/us/en/support/articles/agentic-trading-overview/>.

## Project status

Version `0.10.0` makes `t_analysis < t_trade` explicit, records the exact pair command selected on every trade-decision tick, exposes both cadences in the desktop UI, and binds the decision stride into evidence-policy version 6. It retains the credential, menu, account-scope, taskbar, local-installation, and read-only diagnostic work from 0.9.x. Full shared-history daily research aligns 4,147 QQQ/TQQQ/SQQQ sessions from 2010-02-11 through 2026-08-07. The nine-action holdout policy chose cash: 0.00% return and drawdown. On the corrected 40-session intraday benchmark, every tested strategy lost money after modeled costs; the source-faithful first-half-hour rule returned -0.77%. Long-run fixed and volatility-managed TQQQ benchmarks were historically profitable but are Nasdaq exposure, not demonstrated alpha or future-profit evidence. No strategy has a live certificate. See the [0.8 research upgrade](docs/RESEARCH_UPGRADE_2026-08-09.md), [Action Lab methodology](docs/ACTION_LAB.md), and [public release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Contributing and support

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

Licensed under the [Apache License 2.0](LICENSE).
