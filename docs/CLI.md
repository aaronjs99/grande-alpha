# Command-line companion

The CLI is independent of the optional Qt desktop dependencies. For a source checkout, install
`pip install -e .`; install `.[desktop]` only when you also want the graphical workspace.

## Configuration

Normal startup only reads existing settings. It does not create a defaults file, discover a legacy
application directory, or rewrite an older configuration. Inspect settings with
`grande-alpha-cli config show`. If a saved configuration predates the current schema, run
`grande-alpha-cli config upgrade`; it retains a timestamped backup beside the source file before
writing the upgraded copy. The optional `--path` argument keeps recovery work explicit and allows
testing against a disposable copy. To recover data from an older installation, use
`grande-alpha-cli config import-legacy --source <directory>`; it copies only recognized records,
never auto-discovers a directory, and leaves the originals intact.

For the current-strategy gap/owner inventory, run `grande-alpha-cli engine readiness`.
For research-only earnings candidates, see [earnings screening](EARNINGS_RESEARCH.md).
For dynamic stock/ETF research targets and device-only alerts, see [mixed portfolios](MIXED_PORTFOLIO.md).

## Mixed strategy qualification commands

These commands capture data and produce evidence; none grants broker authority:

```powershell
.\cli.ps1 earnings key-set
.\cli.ps1 earnings key-status
.\cli.ps1 earnings fetch --database C:\private\earnings.db --symbol MSFT --dataset EARNINGS_ESTIMATES
.\cli.ps1 earnings normalize --database C:\private\earnings.db --source-sha SHA256 --kind consensus --period 2026-12-31 --basis alpha_vantage_eps --currency USD
.\cli.ps1 earnings record-fact --database C:\private\earnings.db --input C:\private\normalized-fact.json
.\cli.ps1 earnings verify-event --database C:\private\earnings.db --input C:\private\event.json
.\cli.ps1 portfolio replay-report --input C:\private\historical-replay.json
.\cli.ps1 portfolio forward-append --database C:\private\forward.db --input C:\private\frame.json
.\cli.ps1 portfolio forward-report --database C:\private\forward.db --settings C:\private\replay-settings.json
.\cli.ps1 engine qualification-check --certificate C:\private\qualification.json --candidate-digest SHA256
.\cli.ps1 engine authorization-template
.\cli.ps1 engine authorization-check --permit C:\private\authorization.json --account ACCOUNT --scope-digest SHA256
.\cli.ps1 engine autonomous-template
.\cli.ps1 engine autonomous-readiness --candidate C:\private\candidate.json --qualification C:\private\qualification.json --authorization C:\private\authorization.json --earnings-database C:\private\earnings.db --source C:\private\current-research.json
```

The hidden prompt stores the key in Windows Credential Manager. A process-local
`ALPHA_VANTAGE_API_KEY` environment variable takes precedence when deliberately supplied. The key
is never included in command output. Normalized
facts remain linked to the exact raw provider response. See
[mixed production qualification](PRODUCTION_QUALIFICATION.md) for the complete gate.

After every offline artifact passes, `engine run-autonomous` is the mixed stock/ETF foreground
entry point. Its first activation requires an interactive exact phrase after the broker account and
provider contract are verified. It can be authorized while markets are closed and waits without
ordering until the supported regular-hours route opens. It installs no scheduler, and the current
source file must be refreshed by the qualified current-data collector.

`grande-alpha-cli` uses the same sandbox engine, Evidence Lab, gate definitions, glossary, and local
SQLite receipts as the desktop application. Research commands remain non-actuating. The new
`engine run-live` command uses the shared execution controller with a bounded policy and fresh
terminal approval for every ticket; see the [attended live CLI guide](LIVE_CLI.md).
The separate [bounded unattended engine](UNATTENDED_ENGINE.md) uses explicit standing terms,
an evidence gate, and durable local revocation. It does not renew authority after expiry or restart.

## Readiness without irrelevant prerequisites

```powershell
.\cli.ps1 engine readiness
.\cli.ps1 engine readiness --workflow earnings
.\cli.ps1 engine readiness --setup .\operator-setup.json --policy .\session.live-policy.json
```

The default workflow assesses the installed deterministic strategy. AI subscriptions and earnings
datasets are optional enhancements, not prerequisites for that strategy. The earnings workflow adds
its data and stock-execution qualification requirements. Provider-contract and execution-recovery
requirements are reported once each from the shared runtime assessment. A cloud subscription or
another trader's setup is not required; reliable execution and alerts still need qualification.

An optional setup JSON object may contain `trading_capital_usd` and `daily_loss_threshold_usd` as
positive numbers or null. Only those two fields are read into the report. No account details or
purported permission fields are echoed or honored. Recorded amounts are shown as preferences,
not active limits. Files must be supplied explicitly; private setup files are not auto-discovered.

With `--policy`, the full live-policy schema is validated offline, including its current strategy
fingerprint, route, and any recorded capital/loss ceilings. This clears only the policy requirement
when it matches. It does not contact Robinhood, arm the engine, change saved settings, or certify
unattended operation. Malformed or ambiguous inputs fail rather than manufacturing a pass.

## Headless shadow runner

Run the existing strategy continuously against Robinhood reads, without opening the desktop:

```powershell
.\cli.ps1 engine run --connect --duration 300
# Until Ctrl+C (foreground; no scheduler or automatic restart):
.\cli.ps1 engine run --connect
```

This is **virtual trading, not live autonomous execution**. The read-only broker wrapper blocks
review, placement, and cancellation. The runner shares the desktop instance lock and audit store,
checkpoints on orderly shutdown, and exits on read failures or request timeouts. Force termination
or power loss cannot guarantee a final checkpoint. It never cancels existing real orders.
An open terminal and an awake, connected computer are required. This is not a 24/7 hosting service.
Configuration is reused without enabling saved live permissions. A cash strategy stays in cash.

Cached authentication is required by default. Add `--authenticate` only when present to complete
OAuth in a browser. No credentials are supplied on the command line.

[Robinhood's Agentic Trading documentation](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
allows explicitly authorized autonomous orders and documents Claude Code and Codex CLI connections.
That provider capability does not certify this application's execution implementation. The current
shadow command has no live mode and no LLM decision stage. Use `activation` to inspect the existing
evidence and execution blockers; do not treat successful shadow operation as proof of profitability.

### Connecting Claude Code separately

Robinhood documents this setup command (run only when you intend to give Claude access):

```text
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
```

Then use `/mcp` in Claude Code, select `robinhood-trading`, and authenticate. This connects
Claude directly to Robinhood; it does **not** route trades through GRANDE Alpha's risk controls,
receipts, or instance lock. Do not operate two independent agents on the same trading account.
GRANDE Alpha's runner instead uses its existing MCP adapter directly. MCP is the broker interface;
the CLI is a way to operate an agent, not a separate trading strategy or reliability guarantee.

### Inspect the actual broker contract

```powershell
.\cli.ps1 engine inspect-broker --connect
.\cli.ps1 engine inspect-broker --connect --tool place_equity_order --tool cancel_equity_order
```

This performs MCP initialization and tool discovery only: no account reads, order reviews,
placements, or cancellations. Cached OAuth credentials are required unless `--authenticate`
is explicitly passed. Output includes the observation time and SHA-256 of the complete tool
metadata, including descriptions. The default lists names only; `--tool` includes selected full
contracts, while `--full` can produce very large output. Provider text is external data, not an
authorization grant or an instruction to the application.

Observed 2026-09-07 UTC: the placement tool defaults to review and explicit confirmation, with
an explicit-user-request exception for skipping review. Cancellation requires user confirmation.
This qualifies the broader autonomy language in the public guide; unattended cancellation authority
has not been established. Recheck the live metadata before relying on this dated observation.
Connecting successfully does not resolve the application's other evidence and lifecycle blockers.

After `setup.ps1`, the repo-local wrapper is the easiest entry point:

```powershell
.\cli.ps1 status
.\cli.ps1 activation --width 150
.\cli.ps1 evidence show
.\cli.ps1 evidence show --failures-only --width 150
.\cli.ps1 glossary "Deflated Sharpe"
.\cli.ps1 plans
.\cli.ps1 receipts --limit 20
```

The installed command is also available as `grande-alpha-cli` when its Python Scripts directory is
on `PATH`. Every table wraps to the terminal width; `--width N` gives explicit control without
truncating long evidence requirements.

`plans` prints the same built-in Community entitlement and Pro-coming-soon roadmap as the desktop
dialog. `plans --json` reports that checkout and paid entitlement are unavailable. The optional
`GRANDE_ALPHA_UPGRADE_URL` value is identified as an information link only; it cannot change access.

If PowerShell blocks local scripts, use the signed-system PowerShell wrapper through the provided CMD
launcher:

```powershell
& ".\GRANDE Alpha CLI.cmd" activation --width 150
```

## Activation assistant

`activation` reads only local configuration and the latest evidence receipt. It labels every
condition as `APP CHECK`, `APP GATE`, `APP + YOU`, `YOU`, `RESEARCH`, or `EXTERNAL REVIEW`, prints the
exact next action, and expands every failed evidence gate. It does not connect to Robinhood and has no
command that grants, schedules, reviews, places, or cancels orders.

After offline conditions are resolved, use **Live Readiness** in the normal GUI for fresh
connected-account and quote checks. See the complete [activation checklist](ACTIVATION_CHECKLIST.md).

## Read-only broker readiness

The desktop **Run safe checks** action invokes the read-only broker diagnostic.

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

An attested exact runtime trace has a separate, range-bound source path. `--start` and `--end`
are inclusive U.S. equity trading dates and are mandatory for an Evidence Lab trace run:

```powershell
& ".\GRANDE Alpha CLI.cmd" evidence run `
  --source runtime-trace `
  --database "$env:LOCALAPPDATA\GRANDEAlpha\grande_alpha.db" `
  --bar-seconds 5 `
  --session regular_hours `
  --start 2026-08-20 `
  --end 2027-03-12 `
  --manifest "C:\data\grande-runtime-20260820-20270312.manifest.json" `
  --width 150
```

This explicit `evidence run` is the only runtime-trace CLI path that enters the normal Evidence Lab
service. If the range is input-ready, that service records trials and may reserve, freeze, claim, and
consume the one-use final holdout. Audit and template commands below never do so.

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

## Audit data before Evidence Lab

The data audit reads caches, a supplied CSV, and the evidence-ledger inventory without registering a
trial or reserving/revealing a final holdout:

```powershell
.\.venv\Scripts\python.exe -m grande_alpha.cli data audit --target-interval 5s --width 150
.\.venv\Scripts\python.exe -m grande_alpha.cli data manifest-template --target-interval 5s
```

For a supplied file, `--interval` is mandatory because the command never guesses or relabels cadence.
Add `--manifest` to bind exact source, license attestations, construction method, native resolution,
coverage, and hashes. See [Observed-data readiness](DATASET_READINESS.md) for the complete schema and
one-use sealed-holdout checklist.

### Exact runtime quote traces

Audit a selected SQLite range without a broker call, evidence trial, or holdout action:

```powershell
& ".\GRANDE Alpha CLI.cmd" data runtime-trace audit `
  --database "$env:LOCALAPPDATA\GRANDEAlpha\grande_alpha.db" `
  --bar-seconds 5 `
  --session regular_hours `
  --start 2026-08-20 `
  --end 2027-03-12 `
  --width 150
```

Then print a template bound to that exact date range, selected source rows, canonical dataset hash,
and source-trace hash:

```powershell
& ".\GRANDE Alpha CLI.cmd" data runtime-trace manifest-template `
  --database "$env:LOCALAPPDATA\GRANDEAlpha\grande_alpha.db" `
  --bar-seconds 5 `
  --session regular_hours `
  --start 2026-08-20 `
  --end 2027-03-12
```

The template is printed only; the command does not create a file. Its rights attestations default to
`false`. Save a copy outside the command, review the actual provider/product terms, complete only
truthful fields, then rerun `data runtime-trace audit` with `--manifest PATH`. A changed range or
source row invalidates the old manifest instead of silently expanding the evidence dataset.
`manifest-template` requires both dates, and a manifest-backed audit also requires the matching
`--start` and `--end`. An unbounded audit is collection-progress reporting only; it cannot define an
Evidence Lab input range.
