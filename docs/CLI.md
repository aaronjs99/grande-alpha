# Command-line companion

`grande-alpha-cli` uses the same sandbox engine, Evidence Lab, gate definitions, glossary, and local
SQLite receipts as the desktop application. It is a research and inspection companion: there is no
CLI command that bypasses the desktop application's visual permission review, exact typed phrases,
matching evidence certificate, bounded live-session grant, or broker risk checks.

After `setup.ps1`, the repo-local wrapper is the easiest entry point:

```powershell
.\cli.ps1 status
.\cli.ps1 evidence show
.\cli.ps1 evidence show --failures-only --width 150
.\cli.ps1 glossary "Deflated Sharpe"
.\cli.ps1 receipts --limit 20
```

The installed command is also available as `grande-alpha-cli` when its Python Scripts directory is
on `PATH`. Every table wraps to the terminal width; `--width N` gives explicit control without
truncating long evidence requirements.

## Read-only broker readiness

The companion Morning Check invokes a separate read-only broker diagnostic:

```powershell
.\Morning Check.cmd
```

It discovers accounts and fails unless exactly one active Agentic account exists. Through a
structural read-only broker facade, it fetches only that account's portfolio, positions, orders, and
the exact QQQ/TQQQ/SQQQ quote batch. Each quote must contain valid prices, its matching symbol, a
fresh venue timestamp, and bounded timestamp skew. Recognized terminal order states are normalized;
unknown states are reported open for the downstream flat/order-free preflight. The report prints
`Read-only boundary: ENFORCED (review/place/cancel blocked)` and `Write tools called: 0`.

This diagnostic may trigger provider OAuth, whose granted scope can be broader than the calls made by
the diagnostic. It does not grant or restore live authority, create an evidence certificate, or
claim the account is suitable for trading.

## Run the sandbox

The default source is deterministic and offline. It can verify mechanics but can never create an
eligible live-review certificate.

```powershell
.\cli.ps1 sandbox run --source demo --days 7 --fills 25
.\cli.ps1 sandbox run --source csv --csv .\history.csv --interval 1m
.\cli.ps1 runs
.\cli.ps1 runs --id COMPLETE-RUN-ID
```

All fills are virtual `TQQQS`/`SQQQS` fills. Add `--json` to any inspection command for scripts.

## Run the Evidence Lab

```powershell
.\cli.ps1 evidence run --source demo --days 7
.\cli.ps1 evidence run --source csv --csv .\history.csv --interval 1m
```

The CLI calls the same shared pipeline as the GUI and records the trial ledger and promotion receipt.
It prints all independent gates, then explains every blocker and its next defensible action. Passing
every gate creates only a time-limited local review certificate; it does not start a strategy or
authorize an order.

Community data remains double opt-in. First enable it in **Settings & Permissions**, then include
`--acknowledge-community-data` on the CLI command. This sends only requested symbols, dates, and
intervals; it sends no broker or account data.

## Understanding an evidence count

`8/18 gates passed` (or any partial count from a legacy receipt) is not a percentage of progress
toward trading. The current policy is conjunctive: every canonical gate must
pass on one eligible run. Synthetic source, inadequate breadth, weak trial-adjusted statistics, or a
missing walk-forward test cannot be averaged away by strong execution-cost or drawdown results.
