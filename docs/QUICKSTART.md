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

## Morning check

Double-click **Morning Check.cmd** before opening the app. It launches a read-only check using a
process-scoped PowerShell bypass, so it works even when local `.ps1` files are blocked by the
machine's normal execution policy. It verifies the source runtime, taskbar identity, stored OAuth
path, Robinhood read path, and local evidence state. It never reviews, places, or cancels an order.

A successful result ends with `READY FOR RESEARCH AND LIVE SHADOW`. Real-order controls remain
locked unless the exact current strategy has a fully passing evidence certificate and the user later
completes a separate bounded live-session confirmation.

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
- **Help** explains quick start, account scope, privacy, safety locks, version ownership, and opens the
  searchable terminology glossary with `F1`. Dashed-underlined labels also show the same definitions
  on hover or click.

Every data-table column can be resized by dragging its header boundary. Right-click any header to fit
one column, fit all columns, or restore the defaults; **View → Reset Window & Table Columns** also restores the
default widths. Compact fields such as Status start narrow so Observed and Requirement have more room.

For the matching terminal research and inspection surface, see the [command-line companion](CLI.md):

```powershell
.\cli.ps1 status
.\cli.ps1 evidence show --width 150
```

To authenticate and validate the complete provider read path without invoking any write method:

```powershell
.\Morning Check.cmd
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
