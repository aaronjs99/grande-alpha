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

`8/16 gates passed` is not 50% progress toward trading. The policy is conjunctive: every gate must
pass on one eligible run. Synthetic source, inadequate breadth, weak trial-adjusted statistics, or a
missing walk-forward test cannot be averaged away by strong execution-cost or drawdown results.
