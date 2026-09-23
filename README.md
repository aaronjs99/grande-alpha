# GRANDE Alpha

[![CI](https://github.com/aaronjs99/grande-alpha/actions/workflows/ci.yml/badge.svg)](https://github.com/aaronjs99/grande-alpha/actions/workflows/ci.yml)
[![Python 3.11–3.12](https://img.shields.io/badge/python-3.11–3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows)](docs/WINDOWS_INSTALLATION.md)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**A local-first Windows workstation for leveraged-ETF strategy research, evidence review, and
consent-gated execution experiments.**

![GRANDE Alpha desktop workspace](docs/images/audit/responsive-after-02-main-1366x768.png)

GRANDE Alpha combines deterministic replay, historical evaluation, live shadow observation, an
Evidence Lab, and auditable broker controls in one desktop application. Research works without a
broker account. Every external connection and real-order capability is a separate, revocable opt-in.

> [!WARNING]
> GRANDE Alpha is experimental software, not investment advice. The included strategies are
> engineering baselines, not demonstrated profitable systems. Leveraged and inverse ETFs pursue
> daily objectives and can lose substantial value. You can lose the entire amount traded.

GRANDE Alpha is independent software. It is not affiliated with or endorsed by Robinhood Markets,
ProShares, Nasdaq, any broker, exchange, or fund sponsor.

## Product maturity

GRANDE Alpha follows semantic versioning. The `0.x` line is a community preview: core workflows are
implemented and tested, but compatibility, distribution, provider approval, and operational-support
gates remain open. Version `1.0.0` will mean that every published 1.0 release criterion is satisfied;
it will not mean that any strategy is profitable.

| Product area | 0.x product contract |
|---|---|
| Local research and replay | Available |
| Evidence Lab and audit receipts | Available |
| Live shadow observation | Available; submits no orders |
| Supervised real-order experiment | Integrated source path; attended, hard-capped, and confirmed per order |
| Autonomous directional trading | Fail-closed unless the exact deployment satisfies every evidence and runtime gate |
| Bounded unattended runner | [Explicit standing sessions](docs/UNATTENDED_ENGINE.md); evidence-gated, offline-tested, not live-qualified |
| Mixed PEAD plus leveraged-ETF engine | Implemented behind [production qualification](docs/PRODUCTION_QUALIFICATION.md); not activated or evidence-qualified |
| Automatic startup or Windows-scheduled execution | Not included |
| Community plan | Free and local; no account, checkout, or entitlement server |
| Pro plan | Roadmap only; no paid activation exists |
| Stocks and crypto Agent Desk | Research proposals and local observations only; no automatic multi-market orders |

See the [product contract](docs/PRODUCT_CONTRACT.md), [roadmap to 1.0.0](docs/ROADMAP_TO_1_0.md),
[versioning policy](docs/RELEASE_PROCESS.md#versioning-policy), and [changelog](CHANGELOG.md).

> [!IMPORTANT]
> Source integration is not permission to distribute a broker-connected product. Public Robinhood
> connectivity remains blocked pending written provider approval for the intended product, users,
> order flow, branding, data handling, and distribution model. Individual operators are also
> responsible for account eligibility and applicable legal, tax, and financial requirements.

## Why GRANDE Alpha

- **Research without broker access.** Run deterministic scenarios or permitted CSV data entirely
  locally.
- **Evidence before authority.** Provenance, cost stress, chronological holdouts, fingerprinting,
  and runtime-parity checks fail closed.
- **Realistic replay.** Model spread, latency, partial fills, rejection, volume constraints, and
  cash-T+1 settlement.
- **Live shadow mode.** Observe current quotes and produce fictional fills without an order path.
- **Explicit consent.** Supervised experiments require a bounded session and a fresh confirmation
  for every exact broker-reviewed ticket.
- **Auditable controls.** Append-only receipts, idempotency references, reconciliation, and scoped
  stop/cancel previews make state visible.
- **Local-first privacy.** No first-party telemetry, advertising, or cloud-sync service.
- **Responsive desktop UI.** Tested at portrait, constrained landscape, standard desktop, and wide
  desktop sizes.

## Install from source

Requirements:

- Windows 10 or 11 (64-bit)
- Python 3.11 or 3.12
- PowerShell 5.1 or newer

From PowerShell in the repository directory:

```powershell
.\grande.ps1 setup
.\grande.ps1 run
```

The source checkout has one main command: `grande.ps1`. Use `install` to add Desktop and Start
Menu shortcuts (it runs setup), `doctor` to inspect the installation, and `cli` for terminal
research commands. `verify`, `build`, and `release` are development and packaging tasks.

The app starts in research mode. Broker access and real-order controls remain disabled until enabled
separately in **Settings**.

For installation details, unsigned-build limitations, and credential recovery, read the
[Windows installation guide](docs/WINDOWS_INSTALLATION.md). Public binary distribution remains
blocked until the exact release artifact is code-signed and passes the
[release checklist](docs/RELEASE_PROCESS.md#public-release-checklist).

### Command line without the desktop

The command-line companion has no Qt dependency. For research or automation that does not open the
desktop workspace, install the core package and run `grande-alpha-cli`:

```powershell
py -3.11 -m pip install -e .
grande-alpha-cli --help
```

Install the desktop workspace explicitly with `py -3.11 -m pip install -e ".[desktop]"`.

The Agent Desk supports watchlist and saved-scan research, supported crypto-pair observations,
optional local analysis, and an activity view. Its cash-limit plan and recovery status do not grant
trading authority. See the [Agent workspace](docs/AGENT_WORKSPACE.md) and
[execution journal](docs/AGENT_EXECUTION_JOURNAL.md) for scope and remaining work.

Use the top-bar appearance toggle or **View → Dark mode** to switch the app-wide theme. **STOP +
CANCEL** stops local automation before checking broker orders; cancellation of exact GRANDE-owned
orders still requires confirmation. Filled positions and unrelated orders remain untouched.

## First research run

1. Launch GRANDE Alpha and review the first-run disclosures.
2. Leave every optional external capability disabled.
3. Open **Research**.
4. Run the deterministic scenario or import data you are permitted to use.
5. Inspect the assumptions, execution events, provenance, trades, and failed evidence gates.
6. Treat every result as model evidence—not a prediction of future returns.

See the [quickstart](docs/QUICKSTART.md) for broker-safe checks, desktop navigation, and the CLI.

## Safety model

Research, broker reads, supervised orders, and autonomous authority are separate layers:

```text
Local research ──► optional broker reads ──► live shadow (no orders)
                              │
                              ├──► supervised session + confirmation for every ticket
                              │
                              └──► evidence certificate + runtime parity + bounded authority
                                   (available only when every exact gate passes)
```

Important boundaries:

- Every launch starts without money-moving authority.
- Enabling a capability does not create a live session or place an order.
- **Stop / cancel…** locks new local requests, previews only GRANDE-owned nonterminal orders, and
  requires confirmation before sending cancellation requests.
- Cancellation is best effort and does not reverse a fill or liquidate a position.
- Broker and venue state remain authoritative.

Read [Safety and compliance](docs/SAFETY_AND_COMPLIANCE.md),
[supervised experimental mode](docs/LIVE_ACTIVATION.md), and
[bounded autonomous authority](docs/UNATTENDED_ENGINE.md) before enabling real-order controls.

## Command-line companion

The CLI exposes research, evidence, receipts, and headless operation. `engine run` is read-only
shadow; `engine run-live` is an **attended** real-order session with explicit limits and per-ticket
terminal approval. The legacy `run-unattended` path is bounded and evidence-gated but not
provider-qualified. The mixed engine now has an explicit `run-autonomous` foreground entry point,
but remains closed until its exact production certificate, authorization, current-data source and
real-world provider checks pass. See the [production qualification guide](docs/PRODUCTION_QUALIFICATION.md).

```powershell
.\grande.ps1 cli --help
.\grande.ps1 cli status
.\grande.ps1 cli evidence show --width 150
.\grande.ps1 cli plans --json
```

See the [CLI reference](docs/CLI.md).

## Documentation

| Goal | Start here |
|---|---|
| Install and run locally | [Quickstart](docs/QUICKSTART.md) · [Windows installation](docs/WINDOWS_INSTALLATION.md) |
| Understand current readiness | [Activation checklist](docs/ACTIVATION_CHECKLIST.md) · [Live activation](docs/LIVE_ACTIVATION.md) |
| Research a strategy | [Sandbox](docs/SANDBOX.md) · [Data readiness](docs/DATASET_READINESS.md) · [Evidence Lab](docs/EVIDENCE_LAB.md) |
| Understand execution controls | [Architecture](docs/SYSTEM_ARCHITECTURE.md) · [Trading sessions](docs/TRADING_SESSIONS.md) |
| Understand the product boundary | [Product contract](docs/PRODUCT_CONTRACT.md) · [Roadmap to 1.0.0](docs/ROADMAP_TO_1_0.md) |
| Build or publish a release | [Release and versioning](docs/RELEASE_PROCESS.md) |
| Troubleshoot | [Troubleshooting](docs/TROUBLESHOOTING.md) · [Support](SUPPORT.md) |
| Browse everything | [Documentation index](docs/README.md) |

## Community and Pro

The complete local product is currently available on the **Community** plan for `$0`, without a
GRANDE Alpha account, payment method, or license server. Pro is a coming-soon product direction for
convenience, organization, scale, and optional services. Safety, evidence, provenance, privacy,
stop, and consent controls will not be paywalled. See [Community and Pro plans](docs/PRODUCT_CONTRACT.md#community-and-pro-plans).

## Development

The executable Python package is kept in
[`src/grande_alpha/`](src/grande_alpha/). Its internal `broker`, `ui`, and
`assets` modules separate external adapters, presentation, and packaged
resources from the domain services. The repository-root PowerShell files are
only Windows entry points.

```powershell
.\grande.ps1 setup
.\grande.ps1 verify
```

The source check does not connect to a broker or place an order. Before opening a pull request,
read [Contributing](CONTRIBUTING.md).

## Project links

- [Documentation](docs/README.md)
- [Changelog](CHANGELOG.md)
- [Support](SUPPORT.md)
- [Security policy](SECURITY.md)
- [Privacy](PRIVACY.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Apache License 2.0](LICENSE)
