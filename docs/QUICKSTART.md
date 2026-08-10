# Quickstart

## Source application (works without a GRANDE Alpha signing certificate)

```powershell
.\setup.ps1
.\doctor.ps1 -Full
.\install-local.ps1
.\run.ps1
```

The launcher uses the trusted Python installation on the machine. Review
[Windows installation](WINDOWS_INSTALLATION.md) before distributing a binary.

`install-local.ps1` gives the Desktop and Start-menu shortcuts the same Windows identity as the
running app, and `doctor.ps1` verifies it. If a generic Python or PowerShell item was pinned before
installation, unpin that stale item once, launch **GRANDE Alpha** from the installed shortcut, and
pin the branded GRANDE Alpha button.

## Research session

1. Start with `.\run.ps1` or `Start GRANDE Alpha.cmd`.
2. Read the first-run disclosures and leave every optional capability off.
3. Open **Research Sandbox**.
4. Use the deterministic scenario or import CSV data you are permitted to use.
5. Run a baseline replay, cost stress, parameter sensitivity, random-entry control, and walk-forward evaluation.
6. Inspect trades, execution events, data source, hash, assumptions, and failed gates.

This workflow never connects to a broker and never places an order.

A synthetic scenario cannot produce an eligible certificate. A qualifying historical run must pass
every gate, and its strategy fingerprint must match the current application strategy. Treat a pass
as permission to review risk—not as evidence that the next trade will be profitable.

## Optional capabilities

Open **Settings & Permissions** to enable capabilities one at a time:

- **Broker connection** allows provider-exposed read data and OAuth storage.
- **Real-order automation** is a second permission and requires a current matching Evidence Lab certificate plus an exact typed phrase.
- **Community remote market data** sends symbol/time-range queries to an unsupported external endpoint.
- **Personal ledger** shows a local planning ledger; it never transfers funds.

**Automatic order route defaults** chooses regular, extended, or 24 Hour Market behavior without
granting authority. The choice appears again in every live-session confirmation. Extended and
overnight routes are whole-share limit-only; a route without matching session-complete evidence
remains locked. Read [Trading sessions and order routes](TRADING_SESSIONS.md) before changing the
regular-hours market GFD default.

Removing broker permission disconnects the adapter. Removing real-order permission stops the strategy, revokes live authority, and attempts cancellation. Stored OAuth credentials can be forgotten from the same dialog.

## Desktop navigation

The always-visible menu bar keeps infrequent controls out of the trading header:

- **File** exports redacted diagnostics, opens Settings & Permissions, or exits.
- **View** switches workspaces with `Ctrl+1` through `Ctrl+5`, resets the layout, or uses `F11` full screen.
- **Broker** connects or disconnects Robinhood, refreshes with `F5`, controls live shadow, or forgets the locally stored OAuth credential after confirmation.
- **Research** opens each sandbox result surface directly.
- **Safety** exposes only evidence-gated live controls plus the stop/cancel and flatten paths.
- **Help** explains quick start, account scope, privacy, safety locks, and version ownership.

To authenticate and validate the complete provider read path without invoking any write method:

```powershell
.\doctor.ps1 -Broker
```

This may open Robinhood in your browser. It reads account discovery, one portfolio response,
QQQ/TQQQ/SQQQ quotes, positions, and orders, then disconnects. It does not review, place, or cancel
an order. Robinhood's current consent screen grants a broader provider scope that can include trading
and watchlist access; "read-only" describes this diagnostic's behavior, not the provider's OAuth scope.

## Source checkout

```powershell
.\setup.ps1
.\verify.ps1
.\run.ps1
```

Use `.\build.ps1` for an unsigned local Windows candidate and `.\release.ps1` for both an explicitly
labeled unsigned candidate and a runnable source bundle. Do not distribute the executable as a
finished public binary until it has a valid Authenticode signature.
