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

Removing broker permission disconnects the adapter. Removing real-order permission stops the strategy, revokes live authority, and attempts cancellation. Stored OAuth credentials can be forgotten from the same dialog.

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
