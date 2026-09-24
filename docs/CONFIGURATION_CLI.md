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
dollar defaults. The desktop edits a chosen candidate copy and requires review before Start.
Never store an API key, OAuth token, or account password in configuration or the repository.

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
.\grande.ps1 cli records upgrade-execution-store --audit C:\private\GRANDEAlpha\grande_alpha.db --legacy-equity C:\private\GRANDEAlpha\equity_v1.db --backup-dir C:\private\GRANDEAlpha\upgrade-backups
```

Research targets, replay fills, and shadow fills are not broker orders. Saved activity and
notifications remain local. See [research](RESEARCH.md) for data provenance and cost assumptions.
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

There is one session runner. `--mode` and `--strategy` must be explicit:

```powershell
.\grande.ps1 cli session run --mode shadow --strategy etf --connect
.\grande.ps1 cli session run --mode attended --strategy etf --policy C:\private\policy.json --connect
.\grande.ps1 cli session autonomous-template
```

The mixed runner requires `--mode autonomous --strategy mixed`, `--candidate`, `--authorization`,
`--earnings-database`. It uses internal broker-quote and stored-earnings inputs;
there is no manually refreshed research-file argument. First authorization displays the exact
account, universe and limits and requires a typed phrase in an interactive terminal. It may be
approved before the market opens. The autonomous mixed command starts a user-owned hidden worker;
attended and shadow sessions remain foreground. Closing the starting terminal does not prove the
mixed worker stopped. Its local substitute tests do not establish installed Windows or
provider-observed readiness. Do not deploy it unattended until the remaining checks in the
[capability matrix](USER_GUIDE.md#what-is-implemented-tested-and-still-pending) are complete.

Use `session stop` to fence new submissions without claiming to cancel broker orders.
Use `session status`, `session review`, `session authorize`, `session start`, and `session shutdown`
to control the same mixed worker from another terminal; `session stop` writes a durable local
stop fence even if worker communication fails. `research mcp enable|disable|status` controls the
separate read-only research bridge.
Use `session authorization-revoke --permit <path> --account <number>` to revoke the persistent
mixed permit. `session loss-recovery-ack --database <local-audit-db> --account <number>` clears a
manual recovery pause after a typed confirmation; it does not reset that trading day's loss.

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
| `engine run` | `session run --mode shadow --strategy etf` |
| `engine run-live` | `session run --mode attended --strategy etf` |
| `engine run-autonomous` | `session run --mode autonomous --strategy mixed` |
| `engine run-unattended` | Removed; use the mixed autonomous path after its checks pass |
| `engine stop` | `session stop` |
| `status`, `runs`, `receipts`, `notifications` | `records <name>` |

Configuration file formats and execution permissions are independent of CLI spelling. Renaming a
command never migrates an account, widens an approval, or establishes live readiness.
