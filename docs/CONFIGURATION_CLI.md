# Configuration and command line

Run commands from the repository root with `py -3.11 -m grande_alpha.cli` or
`.\grande.ps1 cli`. The core command line does not import Qt. Use `--help` at each level for the
complete option list. The six top-level groups are `broker`, `data`, `research`, `session`,
`records`, and `config`.

## Configuration and secrets

```powershell
.\grande.ps1 cli config show
.\grande.ps1 cli config upgrade
```

Reading a configuration path does not create or upgrade it. Settings are saved in named `broker`,
`data`, `strategy`, `execution`, `risk`, `storage`, and `desktop` sections. An existing flat file
requires the explicit `config upgrade` command, which validates it and retains a timestamped
backup before replacing it. Unknown sections and settings are rejected. Mixed execution
limits live in a separately approved candidate file and require an explicit account, universe,
financial caps, quote freshness, and loss-recovery policy. The mixed candidate template has no
dollar defaults. Saved `risk.default_max_*` preferences are unfilled in a new installation and
never grant live limits; existing saved values remain readable but are not execution authority.
The desktop edits a chosen candidate copy and requires review before Start.
Never store an API key, OAuth token, or account password in configuration or the repository.
Settings reads, saves and explicit upgrades reject duplicate JSON fields and nonfinite values;
UTF-8 files with a byte-order mark are accepted. Ambiguous existing files remain untouched.

The Alpha Vantage key can be stored with a hidden prompt:

```powershell
.\grande.ps1 cli data earnings key-set
.\grande.ps1 cli data earnings key-status
```

`data earnings fetch` caches successful raw responses and reserves one local quota unit *before*
each external request. The default is at most 25 attempts in a rolling 24-hour window, with a
12-hour cache. A failed request still consumes its reservation. This is a local safeguard, not a
provider guarantee. Exact future earnings consensus must be captured before the announcement;
old post-event estimates cannot be relabeled as pre-event evidence.

## Research and records

```powershell
.\grande.ps1 cli research portfolio template
.\grande.ps1 cli research sandbox run --help
.\grande.ps1 cli data audit --help
.\grande.ps1 cli records status
.\grande.ps1 cli records receipts --limit 20
.\grande.ps1 cli records notifications --unread
.\grande.ps1 cli records upgrade-execution-store --audit C:\private\GRANDEAlpha\grande_alpha.db --legacy-equity C:\private\GRANDEAlpha\equity_v1.db --backup-dir C:\private\GRANDEAlpha\upgrade-backups
```

Research targets and replay fills are not broker orders. Saved activity and
notifications remain local. `records notifications --unread` reads the on-device inbox;
`--ack <id>` acknowledges one entry. GRANDE Alpha sends no notification email. See
[research](RESEARCH.md) for data provenance and cost assumptions.
Records lists, status and evidence inspection open existing databases read-only. Missing records
stay absent, and incompatible databases report an error instead of being initialized or migrated.
Acknowledging a notification and saving research remain explicit writes.

`research sandbox run --no-save` skips the records database entirely. Missing sandbox settings
use defaults; an existing invalid `sandbox_config.json` reports an error and keeps the file
intact. Correct or explicitly replace that file before retrying; invalid input never silently
selects a different strategy. Counts require integers, switches require booleans, and numeric
settings must be finite.
Sandbox settings saves validate first and replace the file atomically; an interrupted write leaves
the previous settings intact.
The separate `grande-alpha-mcp` entry point is a local stdio research interface, not a broker
session runner. It cannot place orders or grant authority and needs explicit per-worker-session
enablement in the desktop or `research mcp enable` CLI command. Configure the compatible client
with the local bridge path shown by `research mcp status` and the `grande-alpha-mcp` entrypoint;
do not expose the bridge file or server as a public network endpoint. See
[Agent research MCP](RESEARCH.md#agent-research-prompts-and-local-mcp).
The execution-store upgrade is an explicit offline operation for existing journals, not a startup
migration. Stop the local worker first. The command refuses to run while the worker's application
lock is held, retains snapshots of both databases, and never creates missing source journals.

## Session commands

There is one live route: the mixed stock/ETF worker. Print an unfilled candidate, supply your
own account, symbols, limits, and earnings database, then review and approve the exact scope:

```powershell
.\grande.ps1 cli session candidate-template
.\grande.ps1 cli session run --candidate C:\private\candidate.json --authorization C:\private\authorization.json --earnings-database C:\private\earnings.db
.\grande.ps1 cli session status
```

The runner requires `--candidate`, `--authorization`, and `--earnings-database`. It uses internal broker-quote and stored-earnings inputs;
there is no manually refreshed research-file argument. First authorization displays the exact
account, universe and limits and requires a typed phrase in an interactive terminal. It may be
approved before the market opens. The command starts a user-owned hidden worker. Closing the
starting terminal does not prove that worker stopped. Source implementation does not establish
installed Windows or provider-observed readiness. Do not deploy it unattended until the remaining
acceptance items in the [capability matrix](USER_GUIDE.md#implemented-behavior-and-remaining-work)
are complete.

Use `session stop` to fence new submissions without claiming to cancel broker orders.
`session status` reports JSON: exit `0` means a worker response was received, and exit `1`
means the worker was unreachable. The latter reads the existing control database in read-only
mode and reports `running: null`, `connected: null`, `recorded_running`, `local_stop_recorded`, and recovery guidance.
An absent or unreadable record never becomes a successful stop or a connected broker. Unexpected
command failures still return exit `2`. A fallback `session stop` returns `stopped: true` only after
committing a fence to a valid existing record, with `worker_reachable: false` and
`broker_verified: false`; it does not create or migrate a database.
Use `session status`, `session review`, `session authorize`, `session start`, and `session shutdown`
to control that worker from another terminal; `session stop` writes a durable local
stop fence even if worker communication fails. `research mcp enable|disable|status` controls the
separate read-only research bridge.
Use `session revoke` to stop and revoke the exact account permit. `session recovery-ack` clears a
manual recovery pause; it does not reset that trading day's loss.

## Command migration

The previous top-level command spellings were removed. This table maps the common actions; use
the new group help for less common research options.

| Before | Now |
|---|---|
| `portfolio plan` | `research portfolio plan` |
| `sandbox run` | `research sandbox run` |
| `evidence run` | `research evidence run` |
| `earnings fetch` | `data earnings fetch` |
| `engine inspect-broker` | `broker inspect` |
| `session run --mode shadow --strategy etf` | Retired; use `research sandbox run` for offline replay |
| `session run --mode attended --strategy etf` | Retired; there is no attended order route |
| `session run --mode autonomous --strategy mixed` | `session run` with candidate, authorization, and earnings paths |
| `session autonomous-template` | `session candidate-template` |
| `session authorization-revoke` | `session revoke` |
| `session loss-recovery-ack` | `session recovery-ack` |
| `engine stop` | `session stop` |
| `status`, `runs`, `receipts`, `notifications` | `records <name>` |

Configuration file formats and execution permissions are independent of CLI spelling. Renaming a
command never migrates an account, widens an approval, or establishes live readiness.
