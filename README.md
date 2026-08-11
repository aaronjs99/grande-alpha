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

Before a market session, double-click **Morning Check** or run `Morning Check.cmd`. It bypasses
machine-wide PowerShell execution-policy changes, verifies the app and taskbar identity, exercises
only Robinhood read methods, and prints the current evidence lock. A green morning check means the
source app and live shadow are operational; it never means the strategy is profitable or authorized
to submit orders.

`build.ps1` writes an **unsigned release candidate** to `dist\GRANDEAlpha`; Windows Smart App Control or enterprise Code Integrity may block it until it is Authenticode-signed with a trusted publisher identity. See [Quickstart](docs/QUICKSTART.md), [Windows installation](docs/WINDOWS_INSTALLATION.md), [Safety](docs/SAFETY_AND_COMPLIANCE.md), [Privacy](PRIVACY.md), and the complete [documentation index](docs/README.md).

Source bundles place their managed Python environment at `%LOCALAPPDATA%\GRANDEAlpha\runtime` to
avoid PySide6 installation failures caused by deeply nested Windows extraction paths. An existing
developer checkout `.venv` remains preferred.

The local installer assigns a stable Windows application identity to both shortcuts so a taskbar pin
keeps the GRANDE Alpha logo instead of adopting the Python or PowerShell host icon. The readiness
doctor reports a mismatch and directs the user to reinstall the shortcuts.

## What it is—and is not

This is not exchange-colocated high-frequency trading. It is a desktop research and consent-gated automation client with a retail low-latency profile: batched quotes target 1-second polling, account truth reconciles every 5 seconds, signals use completed 5-second analysis bars, and the default pair-action decision occurs every 3 bars (15 seconds). Those live 5-second bars are constructed locally from observed quote midpoints; the current remote-history path provides 1-minute bars at its finest interval. A native 1-minute replay and a locally derived 5-second live stream are different datasets and different strategy fingerprints, not interchangeable evidence. Slow provider calls are coalesced instead of queued. Observation speed does not increase the separately bounded live order rate. Its baseline strategy observes QQQ-derived signals and expresses each TQQQ/SQQQ/cash transition in the exact nine-action `(T,S)` command vocabulary. The included strategy is an engineering baseline, not a demonstrated edge. See [Low-latency execution](docs/LOW_LATENCY_EXECUTION.md).

The sandbox models costs, spread, latency, partial fills, volume limits, rejections, `cash_t1` settlement, and purged walk-forward evaluation. Under `cash_t1`, modeled sale proceeds move to unsettled cash, remain part of equity, cannot fund another entry, and return to settled cash only when the next market session is observed. The broker's reported buying power remains authoritative. The sandbox includes a finite, documented research library: EMA momentum, multi-horizon trend, a paper-faithful first-half-hour momentum rule, an older rest-of-day closing hypothesis, opening-range breakout, and a conservative agreement ensemble. The separate nine-action lab represents every `(T,S)` command pair where each leg is sell `-1`, hold `0`, or buy `+1`, trains an auditable offline policy, and evaluates it on a later chronological holdout. It also shows fixed and volatility-managed daily exposure benchmarks. These are hypotheses, not guaranteed edges. Evidence results are recorded locally with a unique candidate-trial ledger and Deflated Sharpe gate. Evidence-policy version 8 reserves one later chronological holdout before development work, freezes it only after all non-holdout gates pass, claims it before evaluation, and consumes it permanently after that single evaluation. Storage independently rechecks the canonical gates, dataset binding, 3x-cost metrics, finite positive risk envelope, and one-promotion rule. A failed final holdout remains consumed; it cannot be rerun into a pass. Only a result that passes every current gate can unlock the separate live-review workflow for the same strategy fingerprint, bar interval, and tested risk envelope; Action Lab results cannot unlock trading. A certificate expires after 30 days and still does not predict future returns or itself authorize an order. Any runtime-settings change revokes an armed grant, and the exact certificate is rechecked before each automatic decision, broker review, and placement call.

All desktop tables have manually adjustable columns and contextual sizing controls. The
[`grande-alpha-cli`](docs/CLI.md) companion exposes the same sandbox engine, Evidence Lab gate table,
saved runs, receipts, and glossary with wrapping output and JSON mode. It intentionally has no command
that bypasses the desktop application's bounded live-session consent workflow.

## Broker integration

The optional adapter uses Robinhood's official Trading MCP endpoint and browser OAuth. Robinhood states that a connected third-party agent can read data across Robinhood accounts, while trading is restricted to the dedicated Agentic account. The provider consent is broader than the app's read-only diagnostic: that diagnostic never invokes order review, placement, cancellation, or watchlist tools, but the granted OAuth scope can include those capabilities. Review the current provider disclosures before opting in: <https://robinhood.com/us/en/support/articles/agentic-trading-overview/>.

## Project status

Version `0.12.0` adds user-selectable regular, extended, and 24 Hour Market execution profiles while enforcing the provider's actual order matrix. Extended and overnight automation is whole-share limit-only; the exact session, order type, time in force, limit offset, settlement model, and bar cadence are bound into evidence-policy version 8 and the separately confirmed live grant. Version 8 adds a strategy-scoped, one-use sealed final holdout; its audit seal is a hash-and-state lifecycle, not encryption or a claim that the source vendor is correct. Complete overnight evidence requires an appropriate imported dataset, and current 24-hour symbol eligibility is checked before submission. The default remains regular-hours market GFD with `cash_t1` research accounting. Full shared-history daily research aligns 4,147 QQQ/TQQQ/SQQQ sessions from 2010-02-11 through 2026-08-07. The nine-action holdout policy chose cash: 0.00% return and drawdown. On the corrected 40-session intraday benchmark, every tested strategy lost money after modeled costs; the source-faithful first-half-hour rule returned -0.77%. Long-run fixed and volatility-managed TQQQ benchmarks were historically profitable but are Nasdaq exposure, not demonstrated alpha or future-profit evidence. No strategy has a live certificate. Tomorrow's procedure is therefore engineering-only live shadow: no live authorization and no orders. See [trading sessions](docs/TRADING_SESSIONS.md), the [0.8 research upgrade](docs/RESEARCH_UPGRADE_2026-08-09.md), [Action Lab methodology](docs/ACTION_LAB.md), and the [public release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Contributing and support

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

Licensed under the [Apache License 2.0](LICENSE).
