# User guide

GRANDE Alpha is a local Windows application for trading research and controlled broker execution.
It is experimental software, not investment advice. No included strategy has demonstrated future
profitability. Leveraged and inverse ETFs can lose substantial value.

## Start here

Use Windows 10 or 11 and Python 3.11 or 3.12. From the repository root in PowerShell:

```powershell
.\grande.ps1 setup
.\grande.ps1 run
```

`setup` uses the selected system Python; a persistent virtual environment is not required. The
desktop package is optional for command-line use. Opening the desktop does not start the worker
or connect a broker. Broker access begins only after Connect and the broker's own consent.
If you have an earlier flat settings file, run `.\grande.ps1 cli config upgrade` before launching
the desktop. This validates the old file and keeps a backup; startup never converts it silently.
Keep credentials in Windows Credential Manager, not a JSON file or a Git commit.

For a first session, use the sequence **Connect account → Set limits → Review → Start**. Choose
your own account, allowed symbols, order and exposure caps, daily loss amount, and recovery policy.
The mixed candidate template leaves financial limits unfilled. Authorization for the mixed engine
may be recorded outside market hours. The engine waits for a supported regular session and fresh
broker data before considering an order.

Practice trading and historical replay are useful but optional. They are not mandatory activation
periods or profit certificates.

## What is implemented, tested, and still pending

This is the authoritative capability matrix for the current checkout. “Tested” means deterministic
local tests unless provider-observed evidence is explicitly stated.

| Capability | Implemented | Tested locally | Remaining before public/live claim |
|---|---|---|---|
| Desktop mixed-worker control, status, responsive layout, and Stop | Yes | Offscreen and substitute tests | Installed Windows and provider-observed acceptance |
| Optional equity/crypto research and local MCP | Yes, research only | Synthetic/local tests | Client-specific setup; no live order authority or model-performance claim |
| Attended ETF CLI session with per-order confirmation | Yes | Yes | Provider-observed end-to-end acceptance |
| Mixed earnings-stock and TQQQ/SQQQ allocation | Yes | Yes | Current point-in-time earnings coverage and strategy evaluation |
| Mixed background autonomous runner | Yes | Substitute/local tests | Exact provider schemas, order/fill/outage tests, and deployment operations |
| Named saved settings and backed-up flat-file upgrade | Yes | Yes | Existing users must run the explicit upgrade |
| Persistent, exact-scope mixed authorization | Yes | Local and offscreen tests | Provider-observed account acceptance |
| Durable references, deduplicated fills, process lease | Yes | Partial | Provider-observed ambiguous-submission and partial-fill recovery |
| Daily mixed loss stop and recovery delay | Yes | Yes | Live reconciliation across external broker activity |
| User-started hidden mixed worker and local control | Yes in source | Substitute/local tests | Installed Windows operation, restart, and provider-observed recovery |
| Public signed binary or paid plan | No | No | Signing, support, provider and legal review; billing is not included |

The desktop and autonomous mixed CLI use the same user-started hidden worker. Attended ETF and
shadow CLI sessions remain foreground paths. The worker implementation and substitute tests are not installed-Windows
or provider-observed acceptance. Do not interpret an Agent research status as confirmation that
the mixed worker is active or authorized.

## Optional research connection

After reviewing a connected account, expand **Research MCP** and enable it for this worker
session, or use `research mcp enable` from the CLI. It starts off, is separate from trading,
and exposes only bounded research context and controls to a compatible local AI client.
Stop, Revoke, or worker shutdown closes the connection and discards queued requests. Already
shared data cannot be recalled; do not put secrets in prompts. See the
[research connection guide](RESEARCH.md#agent-research-prompts-and-local-mcp) and
[privacy policy](../PRIVACY.md).

## Stop, close, and recover

**Stop trading** blocks new local submissions. It does not reverse a sent order, cancel an
accepted order, or sell a filled position. Check the
broker directly after a disconnection, unresolved placement, or shutdown.

When closing a connected desktop window, choose **Keep running in tray**, **Stop trading and exit**,
or **Cancel**. The tray choice leaves the already-started worker running; it is not a Windows
scheduler or startup service. Stop-and-exit completes even when broker cleanup cannot be verified,
and retains local records for later reconciliation.

The mixed runner stores one approval for an exact account, candidate, symbol universe, and limits
until revoked. A changed candidate needs a fresh approval. A restart may use the unchanged approval
only after checking the durable ledger and account lease; it must not blindly repeat an uncertain
order. Manual loss-stop recovery requires an explicit acknowledgement. A timed delay makes recovery
eligible only after its stored deadline, and never resets the same day's loss latch. A loss stop can
allow backed, risk-reducing exits; ordinary Stop or Revoke cannot.

## Local data and privacy

Application records are stored under the current Windows user's `GRANDEAlpha` application-data
directory. Preserve the databases when upgrading: they contain order identities, fills, risk
history, and research evidence. Alpha Vantage access keys use credential storage or a process
environment variable; never paste them into issues or examples. Notifications stay on the device.
The free Community product has no checkout or entitlement service. Pro is a roadmap, not a paid
feature in this release.

## Safety and account responsibilities

Sandbox and shadow sessions do not place broker orders. A saved connection preference is not
trading authorization. Approval applies only to the reviewed account, strategy, symbols, route,
and limits; broker permission and live data checks are separate. The app cannot guarantee fills,
settlement, liquidity, or continuous availability, and a market gap can exceed a local loss limit.
TQQQ and SQQQ target daily leveraged or inverse returns; multi-day returns can differ markedly
from a simple multiple of QQQ. Read the current fund and broker disclosures before using them.

Account eligibility, trading rules, jurisdiction, employment, residency, tax treatment, and use
of another person's capital depend on facts the app does not collect or certify. Consult the
broker and appropriately qualified professionals where needed. Broker confirmations and tax
forms—not the app's estimated P/L—are authoritative. Retain those records, including transfers,
fees, fills, and tax lots. Do not treat an app consent checkbox as legal or tax clearance.

The local calendar handles scheduled sessions, not every emergency closure, halt, or venue
outage. Fresh broker and venue responses remain authoritative. During an outage, use the broker's
own controls to inspect or manage open orders and holdings. App exit, a timed loss recovery, or
a cancellation request is never proof that an order is terminal or a holding is flat.

## Troubleshooting

| Symptom | First check |
|---|---|
| Browser connection does not finish | Complete login only on the broker domain, then return to the local callback; inspect the local log and confirm no second app instance owns the lock. |
| Account or buying power looks wrong | Compare the exact Agentic account in the broker; refresh read-only state before any approval. |
| Quotes or earnings are missing | Check timestamps, symbol coverage, provider permissions, quota, and whether consensus was captured before the announcement. Missing stock allocation stays in cash. |
| A strategy remains idle | Inspect market window, fresh quotes, available buying power, approval scope, loss state, and the selected policy. An idle result is not necessarily an error. |
| Stop leaves a holding | Stop blocks local submissions; selling a filled holding is a separate reviewed action. Verify the broker position. |
| Exit warns about unverified orders | Check the broker directly. The local stop fence does not prove cancellation, and durable records remain for reconciliation. |
| The desktop closes but a session appears active | Check the worker's session status. Keep running in tray and closing a terminal do not stop it; Stop trading and exit writes the local fence. |

Never paste credentials, account identifiers, balances, or unredacted diagnostics into a public
issue. If an installed app behaves differently from the source, update or reinstall the exact
build; changing files does not change a process already running in memory.

See [configuration and CLI](CONFIGURATION_CLI.md) for exact commands,
[architecture](ARCHITECTURE.md) for execution boundaries, and
[development and release](DEVELOPMENT_RELEASE.md) for evidence required before distribution.
