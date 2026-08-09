# Quickstart

## Packaged application

1. Start `GRANDEAlpha.exe`.
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

## Source checkout

```powershell
.\setup.ps1
.\verify.ps1
.\run.ps1
```

Use `.\build.ps1` for a local Windows build and `.\release.ps1` for a reviewed release bundle.
