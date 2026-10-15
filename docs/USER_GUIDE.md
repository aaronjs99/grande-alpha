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

**New session** opens a form; no candidate file has to be written by hand. Its clearly labelled
example uses $10 per order, $100 total exposure, $200 daily traded amount, a $5 daily loss stop,
five orders per day and one per minute, with manual loss recovery and a one-hour window. These are
illustrative settings, not personalized risk advice or a guarantee that losses cannot exceed the
stop. Fill your exact account and allowed symbols, check the Strategy tab, and save the candidate.
**Edit setup** reopens a draft or complete candidate and saves a new copy. Unfilled fields remain
blank; incomplete candidates cannot reach Review or Start. Preparing a file works while disconnected.
The setup panel displays saved limits; **Edit setup** is the one place to change them. Cancelling
the editor preserves the loaded candidate and its review. Saving a changed copy clears the review.

The Agent desk stays open while sessions run. **Session details** shows the latest strategy check
and when the worker last received a market snapshot. A broker response is not proof of a fill;
check orders, fills, balances, and positions directly with Robinhood. **STOP** stays visible.

Practice trading and historical replay are useful but optional. They are not mandatory activation
periods or profit certificates.

## Implemented behavior and remaining work

This is the authoritative capability matrix for the current checkout. “Implemented” describes
source code, not verified live broker behavior.

| Capability | Source status | Remaining before public/live claim |
|---|---|---|---|
| Desktop mixed-worker control, status, responsive layout, and Stop | Implemented | Installed Windows and provider-observed acceptance |
| Optional equity/crypto research and local MCP | Implemented; research only | Client-specific setup; no live order authority or model-performance claim |
| Mixed earnings-stock and TQQQ/SQQQ allocation | Implemented | Current point-in-time earnings coverage and strategy evaluation |
| Mixed background autonomous runner | Implemented in source | Exact provider behavior and deployment acceptance |
| Named saved settings and backed-up flat-file upgrade | Implemented | Existing users must run the explicit upgrade |
| Persistent, exact-scope mixed authorization | Implemented in source | Provider-observed account acceptance |
| Durable references, deduplicated fills, process lease | Implemented in source | Provider-observed ambiguous-submission and partial-fill recovery |
| Daily mixed loss stop and recovery delay | Implemented in source | Live reconciliation across external broker activity |
| User-started hidden mixed worker and local control | Implemented in source | Installed Windows operation, restart, and provider-observed recovery |
| Public signed binary or paid plan | Not included | Signing, support, provider and legal review; billing is not included |

The desktop and CLI use the same user-started hidden mixed worker. The old attended ETF and
shadow-session paths are retired; offline replay and historical records remain. Source
implementation is not installed-Windows or provider-observed acceptance. Do not interpret an
Agent research status as confirmation that the mixed worker is active or authorized.

## Optional research connection

The default Agent desk shows **virtual** equity, paper P&L, simulated fills, closed-trade win rate,
recent chart marks, and NOVA / ORIN / VELA / KADE / RUNE / ZARA stage reports. **Start paper** opens
the Paper setup tab with editable $1,000 starting cash and $100 per entry. These defaults apply
only to virtual money. Press **Start virtual paper** to run the synthetic demo, or choose broker
quotes after connecting and reviewing an account. **Stop paper** retains the virtual journal.

**Research → Paper journal** reads saved virtual fills independently of the worker. The session
picker lists the latest 100 sessions. Each page shows up to 100 fills, newest first; **Older fills**
continues through the complete journal, and **Refresh** returns to that session's latest page.
**Export page** saves exactly the displayed page as CSV, preserving decimal quantities and prices.
Replacing an existing export requires confirmation. The viewer never initializes or resumes a
paper session, and malformed journals produce an error. Saved equity uses saved virtual marks,
which may be stale; virtual fills omit fees and liquidity and do not establish a trading edge.

Broker setup is under **Session → Broker session setup**. It retains the exact review and approval
flow. Desktop Connect and paper startup never recover saved live intent automatically; unresolved
running intent needs Stop and a new review. **Session details** contains full status, guidance,
data coverage, and control logs. **View** switches the desk's light/dark appearance.

Select an **Activity** update to read and copy its full message. Setup tabs adapt to narrow
windows, including long status text. **Repeat** applies only to the synthetic demo and clears
when you switch to broker quotes.

Ollama is optional. Paper trading, broker setup, Review and live controls work with AI off, which
is the default. Opening the app or chat does not start Ollama or load any model.

**AI chat** opens Antonio's conversation view. Enter an installed Ollama model name and send a
question. It receives a bounded paper snapshot and recent messages at loopback only; it cannot
start sessions, apply settings or send orders. Missing models show an error; replies can be
cancelled and retried. Chat requires Ollama to be running separately. Before inference, GRANDE
checks installed-model metadata and available RAM, rejects cloud-routed and embedding-only models,
and uses explicit context and reply limits. It keeps a conservative 2 GiB RAM reserve and allows
one GRANDE request at a time. This is an admission estimate, not a memory guarantee. A model observed
cold is requested with immediate release after the reply; already-loaded models are not explicitly
unloaded. Cancelling a reply stops the local wait; it cannot guarantee the Ollama server has stopped
generating. GRANDE never downloads a model, kills other applications, or changes Ollama settings.

After reviewing a connected account, open **Session details** and enable **research MCP** for this worker
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

## When worker status is unavailable

An unreachable worker appears as **Unknown — worker unreachable** when local running intent
is recorded or cannot be read. Previous strategy checks and market snapshots are marked as
last reported; account, data coverage, research, and paper status are unverified. Use
**Refresh status** or **F5** to retry without starting a worker. Connect and Start stay locked
while running intent is unresolved.

The main **STOP** button can write a durable fence to an existing worker record when communication
fails. **Local stop recorded** confirms that local fence, while worker activity, already-sent
orders, and holdings remain unverified. A later connection requires a new review before Start.
Stop cannot create or repair a missing, corrupt, or incompatible control record; the app reports
that failure. **Stop trading and exit** also attempts the fence after a cold disconnected launch
and distinguishes an unconfirmed stop from a recorded fence.

## Local data and privacy

Application records are stored under the current Windows user's `GRANDEAlpha` application-data
directory. Preserve the databases when upgrading: they contain order identities, fills, risk
history, and research evidence. Alpha Vantage access keys use credential storage or a process
environment variable; never paste them into issues or examples. Warning and error notifications
are kept in the local inbox; view them with `grande-alpha-cli records notifications --unread`.
They are not sent by email.
The free Community product has no checkout or entitlement service. Pro is a roadmap, not a paid
feature in this release.

## Safety and account responsibilities

Sandbox and historical replay do not place broker orders. A saved connection preference is not
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
