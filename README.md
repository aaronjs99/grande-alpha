# GRANDE Alpha

GRANDE Alpha is a local-first Windows desktop workstation for researching leveraged-ETF strategies. It starts in research mode, provides deterministic and historical replay for synthetic `TQQQS`/`SQQQS` instruments, and keeps broker access and real-order controls behind separate, revocable permissions.

> Community preview: this software is experimental, provides no investment advice, and cannot promise profit. Leveraged and inverse ETFs seek daily objectives; results over longer periods can differ materially. You can lose the entire amount traded.

GRANDE Alpha is independent software. It is not affiliated with, endorsed by, or sponsored by Robinhood Markets, Inc., ProShares, Nasdaq, or any broker, exchange, or fund sponsor.

## Public-safety defaults

- First launch opens a disclosure-led onboarding flow; research mode is the default.
- Broker access, remote community market data, the optional personal ledger, and real-order controls are independent opt-ins.
- Broker OAuth credentials are stored through the operating-system credential vault, never in project files.
- Enabling real-order controls requires an explicit settings phrase. Every application launch still starts locked, and each live session needs a separate time-limited confirmation and numeric limits.
- The red stop control revokes local order authority and attempts to cancel open Agentic equity orders. It cannot guarantee cancellation during a network or provider failure and does not liquidate filled positions.
- A redacted diagnostic export is available for support. The application sends no first-party telemetry.

## Start here

For a packaged build, run `GRANDEAlpha.exe`, complete onboarding, and select **Research Sandbox**. For source development:

```powershell
.\setup.ps1
.\run.ps1
.\verify.ps1
.\build.ps1
```

The executable is written to `dist\GRANDEAlpha\GRANDEAlpha.exe`. See [Quickstart](docs/QUICKSTART.md), [Safety](docs/SAFETY_AND_COMPLIANCE.md), [Privacy](PRIVACY.md), and the complete [documentation index](docs/README.md).

## What it is—and is not

This is not exchange-colocated high-frequency trading. It is a desktop research and consent-gated automation client. Its baseline strategy observes QQQ-derived signals and may select TQQQ, SQQQ, or cash. The included strategy is an engineering baseline, not a demonstrated edge.

The sandbox models costs, spread, latency, partial fills, volume limits, rejections, and walk-forward evaluation. No sandbox, evidence-lab, or shadow result automatically grants live authority.

## Broker integration

The optional adapter uses Robinhood's official Trading MCP endpoint and browser OAuth. Robinhood states that a connected third-party agent can read data across Robinhood accounts, while trading is restricted to the dedicated Agentic account. Review the current provider disclosures before opting in: <https://robinhood.com/us/en/support/articles/agentic-trading-overview/>.

## Project status

Version `0.4.0` is a community-preview release candidate. Source tests, packaging, and smoke checks are automated, but public binary distribution still requires maintainer release review, dependency audit, checksums, and code signing where available. See [Public release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Contributing and support

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

Licensed under the [Apache License 2.0](LICENSE).
