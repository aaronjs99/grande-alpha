# GRANDE Alpha

GRANDE Alpha is a local-first Windows desktop workstation for researching leveraged-ETF strategies. It starts in research mode, provides deterministic and historical replay for synthetic `TQQQS`/`SQQQS` instruments, and keeps broker access and real-order controls behind separate, revocable permissions.

> Community preview: this software is experimental, provides no investment advice, and cannot promise profit. Leveraged and inverse ETFs seek daily objectives; results over longer periods can differ materially. You can lose the entire amount traded.

GRANDE Alpha is independent software. It is not affiliated with, endorsed by, or sponsored by Robinhood Markets, Inc., ProShares, Nasdaq, or any broker, exchange, or fund sponsor.

## Public-safety defaults

- First launch opens a disclosure-led onboarding flow; research mode is the default.
- Broker access, remote community market data, the optional personal ledger, and real-order controls are independent opt-ins.
- Broker OAuth credentials are stored through the operating-system credential vault, never in project files.
- Enabling real-order controls requires a current passing Evidence Lab certificate for the exact strategy settings plus an explicit settings phrase. Every application launch still starts locked, and each live session needs a separate typed, non-persistent authority bound to one account, ticker set, route, strategy fingerprint, Eastern-day expiry, and numeric limits. It includes visible pause/revoke controls and hash-chained action receipts; none of these controls guarantees profit.
- **STOP + CANCEL** first locks new local requests and shows an exact, blocking preview of the nonterminal Agentic-account orders owned by GRANDE Alpha's durable intent ledger. It sends cancellation requests only after explicit confirmation, never includes unrelated or manually placed orders, and discloses already-pending cancellations for terminal verification without submitting them twice. Revoke, Settings disable, Disconnect, credential forgetting, and Exit do not silently cancel: they lock or refuse while GRANDE-owned open or unresolved order state remains. A confirmed cancellation can still fail during a network or provider failure and never liquidates a filled position.
- A redacted diagnostic export is available for support. The application sends no first-party telemetry.
- Normal and scheduled runtime default to the deterministic **CASH / hold** champion: flat signal,
  `(0,0)` pair action, and no TQQQ/SQQQ entry. Other runtime policies require a deliberate settings
  selection and remain subject to the same shadow/evidence locks; none is claimed profitable.

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

An optional, explicit [scheduled-shadow setup](docs/SCHEDULED_SHADOW.md) can launch `--auto-shadow`
at a zone-derived weekday time between 7:00 and 9:20 AM ET in supported continental-U.S. Windows
time zones. Local installation adds only the setup shortcut; it never enables
the task silently.

`build.ps1` writes an **unsigned release candidate** to `dist\GRANDEAlpha`; Windows Smart App Control or enterprise Code Integrity may block it until it is Authenticode-signed with a trusted publisher identity. See the [Activation checklist](docs/ACTIVATION_CHECKLIST.md), [Quickstart](docs/QUICKSTART.md), [Windows installation](docs/WINDOWS_INSTALLATION.md), [Safety](docs/SAFETY_AND_COMPLIANCE.md), [Privacy](PRIVACY.md), and the complete [documentation index](docs/README.md).

Source bundles place their managed Python environment at `%LOCALAPPDATA%\GRANDEAlpha\runtime` to
avoid PySide6 installation failures caused by deeply nested Windows extraction paths. An existing
developer checkout `.venv` remains preferred.

The local installer assigns a stable Windows application identity to both shortcuts so a taskbar pin
keeps the GRANDE Alpha logo instead of adopting the Python or PowerShell host icon. The readiness
doctor reports a mismatch and directs the user to reinstall the shortcuts.

## What it is—and is not

This is not exchange-colocated high-frequency trading. It is a desktop research and consent-gated automation client with a retail low-latency profile: batched quotes target 1-second polling, account truth reconciles every 5 seconds, signals use completed 5-second analysis bars, and the default pair-action decision occurs every 3 bars (15 seconds). Those live 5-second bars are constructed locally from observed quote midpoints; the current remote-history path provides 1-minute bars at its finest interval. A native 1-minute replay and a locally derived 5-second live stream are different datasets and different strategy fingerprints, not interchangeable evidence. Slow provider calls are coalesced instead of queued. Observation speed does not increase the separately bounded live order rate. The runtime champion is CASH / hold; deliberate research policies observe QQQ-derived signals and express each TQQQ/SQQQ/cash transition in the exact nine-action `(T,S)` command vocabulary. The included policies are engineering baselines, not demonstrated edges. See [Low-latency execution](docs/LOW_LATENCY_EXECUTION.md).

The sandbox models costs, spread, latency, partial fills, volume limits, rejections, `cash_t1` settlement, and purged walk-forward evaluation. Under `cash_t1`, modeled sale proceeds move to unsettled cash, remain part of equity, cannot fund another entry, and return to settled cash only when the next market session is observed. The broker's reported buying power remains authoritative. The sandbox includes a finite, documented research library: EMA momentum, multi-horizon trend, a paper-faithful first-half-hour momentum rule, an older rest-of-day closing hypothesis, opening-range breakout, and a conservative agreement ensemble. The separate nine-action lab represents every `(T,S)` command pair where each leg is sell `-1`, hold `0`, or buy `+1`, trains an auditable offline policy, and evaluates it on a later chronological holdout. It also shows fixed and volatility-managed daily exposure benchmarks. These are hypotheses, not guaranteed edges. Evidence results are recorded locally with a unique candidate-trial ledger and Deflated Sharpe gate. Evidence-policy version 13 requires manifest-bound observed-data provenance and exact runtime-observation replay under the two-sided bid/ask book-clock v2 contract, reserves one later chronological holdout before development work, fingerprints every material execution/sizing setting, stress-tests every spread component, rejects bypassed forced closes, and blocks non-cash promotion until replay and runtime share the certified execution contract. Storage independently rechecks the canonical gates, dataset/provenance binding, 3x-cost metrics, finite positive risk envelope, and one-promotion rule. A failed final holdout remains consumed; it cannot be rerun into a pass. Only a result that passes every current gate can unlock the separate live-review workflow for the same strategy fingerprint, bar interval, and tested risk envelope; Action Lab results cannot unlock trading. A certificate expires when either its promotion or final market observation exceeds 30 days and still does not predict future returns or itself authorize an order. Any runtime-settings change revokes an armed grant, and the exact certificate is rechecked before each automatic decision, broker review, and placement call.

All desktop tables have manually adjustable columns and contextual sizing controls. The
[`grande-alpha-cli`](docs/CLI.md) companion exposes the same sandbox engine, Evidence Lab gate table,
saved runs, receipts, and glossary with wrapping output and JSON mode. It intentionally has no command
that bypasses the desktop application's bounded live-session consent workflow.

## Broker integration

The optional adapter uses Robinhood's official Trading MCP endpoint and browser OAuth. Robinhood states that a connected third-party agent can read data across Robinhood accounts, while trading is restricted to the dedicated Agentic account. The provider consent is broader than the app's read-only diagnostic: that diagnostic never invokes order review, placement, cancellation, or watchlist tools, but the granted OAuth scope can include those capabilities. Review the current provider disclosures before opting in: <https://robinhood.com/us/en/support/articles/agentic-trading-overview/>.

Scheduled shadow uses one continuously supervised read-only process. It waits outside regular equity
sessions and retries transient provider read failures with bounded backoff during an eligible session.
Continuous process uptime does not mean 24/7 TQQQ/SQQQ trading and cannot grant live-order authority.

## Project status

Version `0.15.0` adds session-scoped live-pilot safety machinery: typed non-persistent authority bound to one Agentic account, exact tickers, route, candidate fingerprint, Eastern-day expiry, and numeric limits; fresh account/quote preflight; durable pre-network placement receipts; restart-restored daily usage; and visible pause/revoke controls. The normal and scheduled runtime champion remains deterministic **CASH / hold**, the runtime execution-parity assessment remains blocked for non-cash candidates, and no strategy currently receives live authority. These controls do not establish an edge or guarantee profit, so scheduled operation remains engineering-only read-only shadow. See the [champion selection report](docs/CHAMPION_SELECTION_2026-08-11.md), [scheduled shadow](docs/SCHEDULED_SHADOW.md), [trading sessions](docs/TRADING_SESSIONS.md), [Action Lab methodology](docs/ACTION_LAB.md), and the [public release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Contributing and support

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

Licensed under the [Apache License 2.0](LICENSE).
