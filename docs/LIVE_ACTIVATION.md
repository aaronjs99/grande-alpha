# Live-pilot activation and external gates

GRANDE Alpha implements separate supervised and evidence-gated autonomous execution paths. Their
presence in the product does not make a deployment eligible for directional trading. Eligibility is
resolved from the exact strategy, evidence, runtime, broker, account, route, consent, and external
requirements at the time authority is requested.

The supported Robinhood order-review contract requires the exact reviewed ticket and market
disclosure to be presented for explicit confirmation before each placement. The desktop has a
separate [supervised experimental mode](#supervised-experimental-real-order-mode) that enforces that confirmation
for every ticket. That attended mode does not convert the strategy to `LIVE_REVIEW_ELIGIBLE` and does
not unlock autonomous placement. The autonomous path remains fail-closed behind its evidence and
runtime-parity requirements.

## Deployment eligibility

- Every installation defaults to deterministic **CASH / hold**, which requests no TQQQ or SQQQ
  position.
- A release may mark an execution or sizing contract uncertified. An uncertified contract cannot
  obtain autonomous directional authority.
- Distributions do not bundle an operator-specific certificate or proof of a positive deployable
  result. Each deployment must establish its own lawful data provenance, evidence, runtime parity,
  broker state, and external eligibility.

Do not represent GRANDE Alpha or any included strategy as profitable, recommended, or universally
ready for autonomous trading. Until every exact gate passes, the evidence-gated runtime remains CASH
or shadow-only research. An
autonomous directional candidate must first pass the development gates, the one-use final holdout,
sizing parity, and a monitored forward-shadow period with positive after-cost evidence. Passing those
gates would permit a separate autonomous review; it would not guarantee future profit. The supervised
experimental route is different: it permits only attended, hard-capped, individually confirmed broker
tickets and is not evidence of an edge or a recommendation.

Activation evidence must use policy v13, runtime-observation schema v2, quote-batch schema v2,
and exact quote validator v2. Policy-v12 receipts and validator-v1 traces predate durable bid/ask
book clocks and are intentionally stale; they cannot unlock live review.

## If broker authorization is revoked

If a check reports that the credential was revoked, do not repeatedly retry it.

1. Revoke local authority so no new request can be authorized. If GRANDE Alpha reports an owned open
   or unresolved order, use **STOP + CANCEL**, inspect its exact preview, explicitly confirm the
   intended cancellations, and wait for terminal verification.
2. Disconnect only after cleanup is clear. Disconnect refuses rather than silently cancelling an
   owned open or unresolved order.
3. Select **Broker → Forget Stored OAuth Credentials…** and confirm removal. Credential forgetting
   is available only from the clean disconnected state; it does not cancel an order or revoke a
   connection inside Robinhood.
4. Select **Connect Robinhood** and complete the new browser consent on Robinhood's site.
5. Refresh and run the read-only broker diagnostic. Confirm exactly one active Agentic account and
   current balances, positions, orders, and exact QQQ/TQQQ/SQQQ quotes.
6. If reconnect still fails, stop. Do not work around OAuth or copy tokens into files. Review
   Robinhood's [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
   and [third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/),
   then contact Robinhood Support.

## What one live session would require

The pilot supports **regular market hours and GFD orders only**. Extended-hours, overnight, or GTC
live authority is rejected even if those routes are available in research views.

One deliberate **Authorize & Start Supervised Session** action inside the regular-session entry
window creates and starts one bounded authority for the same Eastern calendar day. The live signal pipeline
is reset at that moment, so premarket and pre-start observations cannot warm the candidate. The grant
binds exactly one active Robinhood Agentic account, both
TQQQ and SQQQ, the current strategy fingerprint, the regular-hours/GFD route, expiry, and numeric
limits. The grant and typed phrase are never persisted. Restart, expiry, revocation, account change,
or fingerprint change returns the app to `LOCKED` and requires a new explicit authorize-and-start.
Pause/resume may continue the same still-valid grant but cannot extend or alter it.

There is **no automatic live schedule**. Live shadow and either real-order path can be started only
from the running desktop application; GRANDE Alpha installs no Windows scheduled task.

Before either bounded authority is created—and again before live strategy start—the app requires:

- the exact connected Agentic account to be active and freshly reconciled;
- zero real TQQQ/SQQQ position and zero nonterminal Agentic orders;
- no durable or in-memory unresolved placement outcome;
- positive broker-reported account value and buying power;
- exact QQQ, TQQQ, and SQQQ quotes, with matching symbols, valid prices, bounded timestamp skew, and
  age within the grant's quote-age cap.

The **autonomous evidence-gated** path additionally requires a current certificate for the exact
candidate, cadence, route, settlement/sizing contract, and requested risk envelope. The **supervised
experimental** path does not claim that certificate; it remains attended, hard-capped, and requires a
fresh exact confirmation for every reviewed order.

Any failed preflight leaves the relevant real-order path locked.

## Daily budgets survive restart

The candidate's consecutive-loss limit also applies to real-order sessions. Like replay,
it counts each realized losing sell fill, including partial fills, rather than waiting for
an entire position to close. Live results use immutable provider execution IDs, actual
prices and fees, and proportional allocation of the recorded entry cost. Repeated broker
snapshots do not add another loss. The count spans TQQQ and SQQQ in the same Agentic account.

A non-losing fill resets the current streak, but reaching the configured limit latches a
pause on new buys for the rest of that Eastern trading day. Restart, reconnect, and a new
grant do not erase the history. Exits remain available subject to the existing order checks;
the pause itself does not sell or cancel anything. A new Eastern trading day starts a new
streak. Missing or inconsistent entry-cost history blocks new buys. The controller checks
again after confirmation and the final broker refresh. This does not certify that modeled
fills and actual provider fills have identical economics or unlock autonomous trading.

The gross daily-notional cap counts placement invocations for buys **and** sells. The order-count cap
also counts every placement invocation. Before each grant is armed, GRANDE Alpha restores that
same-ET-day usage from append-only receipts; revoking, restarting, or granting a narrower later
session cannot reset the day's budget. Per-order notional, total exposure, session loss, rolling
order rate, spread, and quote-age limits remain independent fail-closed checks.

The count occurs immediately before the broker placement boundary. A timeout, transport failure, or
response without a usable order id is treated as possibly accepted and still consumes the budget.

Every autonomous sell is one-shot. After one known exit placement response, the app revokes live
authority and requires the user to verify current Robinhood inventory before granting a new session.
It does not automatically retry an exit against a potentially stale positions snapshot.

## Ambiguous acknowledgement: quarantine, never retry

When a placement acknowledgement is ambiguous, the app:

1. marks the durable intent `submission_uncertain`;
2. locks and revokes autonomous authority;
3. quarantines that reference from reuse; and
4. requires authoritative order reconciliation before any new authority or resume.

Do **not** click the action again, create a new reference, or manually duplicate the order. Check the
Robinhood order view and reconcile the original intent. Retrying an unknown outcome can create a
duplicate real order.

## Stop, cancel, terminal verification, and exit

**STOP + CANCEL** first locks new local requests and performs a read-only refresh. It then presents a
blocking confirmation with the exact count and details of nonterminal Agentic-account orders linked
to GRANDE Alpha's durable order intents. Unrelated or manually placed orders are outside this scope
and remain untouched. An order already reported as pending cancellation is disclosed and monitored
for a terminal state, but GRANDE Alpha does not submit a duplicate cancellation request for it.

No cancellation request is sent until the user explicitly confirms that exact preview. If the
account or owned nonterminal-order set changes before execution, the preview is stale and the action
must refuse and be reviewed again; its scope cannot silently expand. After confirmation, GRANDE
Alpha requests cancellation only for the reviewed orders and polls broker truth for terminal states.
A cancellation request is not proof of cancellation: it can race a fill, fail remotely, or remain
pending.

**Revoke authority**, disabling a capability in **Settings**, **Disconnect**, credential forgetting,
and **Exit** never substitute for that order-specific confirmation. They lock new local activity and
refuse to complete while a GRANDE-owned open or unresolved order remains, directing the user to the
explicit **STOP + CANCEL** flow. Internal quote, reconciliation, and risk faults follow the same
no-implicit-cancellation boundary.

If any targeted order is missing, nonterminal, or cannot be refreshed, cleanup is unresolved. GRANDE
Alpha remains connected and refuses Disconnect, permission disablement, credential forgetting, and
Exit. Check Robinhood directly, repeat the explicit **STOP + CANCEL** preview if appropriate, and
continue only after terminal verification passes. Filled positions are not automatically liquidated
and remain the user's responsibility.

## Calendar, emergency closure, and halt limits

The local calendar models recurring U.S. cash-equity holidays and scheduled early closes. It is not
an exchange-status feed and cannot anticipate emergency closures, exchange outages, symbol halts,
regulatory suspensions, or every calendar correction. Check the official
[NYSE holidays and trading hours](https://www.nyse.com/markets/hours-calendars) and
[Nasdaq current trading halts](https://www.nasdaqtrader.com/trader.aspx?id=tradehalts).

Stale/missing quotes, broker warnings, an ineligible route, or unresolved order state must fail
closed. Never bypass a lock because a weekday or locally calculated session says the market should
be open. Robinhood and the relevant venue remain authoritative.

## Jurisdiction and public-product release gates

Live automated trading and commercial distribution can raise different account-eligibility, legal,
tax, employment, residency, sanctions, licensing, and business questions for different operators and
jurisdictions. GRANDE Alpha does not infer these facts and cannot determine the answer. The in-app
attestation is a consent checkpoint, not professional advice or clearance. Operators and distributors
must obtain guidance applicable to their exact circumstances before proceeding.

Distributors may expose official local references in **Live Readiness** with the documented
`GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS` setting. Those links remain informational and must not be
presented as an app-issued approval.

Before distributing a public product that connects to Robinhood or exposing a Robinhood-backed API,
obtain written Robinhood approval that expressly covers the intended product, users, order flow,
branding, data handling, and distribution model. Robinhood's official
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/)
states that trading APIs may not be linked without written authorization; also review the current
[Robinhood legal library](https://robinhood.com/us/en/legal/). Until written scope is obtained, a
public release must not offer Robinhood connectivity or imply Robinhood approval, partnership, or
endorsement.

## Unattended and mixed-stock route

The mixed stock/ETF engine has a foreground CLI runner, finite multi-day scope, exact-candidate
authorization, durable order references, duplicate-process exclusion, a local stop latch, and
same-scope restart recovery. These are implemented mechanisms, not a qualified live deployment.
No scheduler, cloud host, remote alert service, or automatic provider-side cancellation is installed.

| Area | Implemented | Still required before relying on real-money autonomy |
|---|---|---|
| Strategy | Causal earnings screen, mixed allocator, replay, forward recorder | Licensed point-in-time observations and passing exact-candidate evidence |
| Broker | MCP adapter, current-contract checks, account and instrument gates | Provider-observed order, fill, rejection, cancellation, and recovery tests |
| Runtime | Foreground runner, finite grants, durable idempotency and local stop | Host outage, restart, resource, and residual-exposure qualification |
| Alerts | Local inbox and terminal notices | An attended response plan; remote delivery is not implemented |
| Distribution | Local source path | Provider approval, data rights, security, support, and legal review |

For a candidate-specific inventory, run `engine autonomous-readiness`. It does not contact the
broker or grant authority. The template contains no personal account or money amount:

```powershell
.\cli.ps1 engine autonomous-template
.\cli.ps1 engine autonomous-readiness --candidate C:\private\candidate.json --qualification C:\private\qualification.json --authorization C:\private\authorization.json --earnings-database "$env:LOCALAPPDATA\GRANDEAlpha\earnings.db" --source C:\private\current-research.json
.\cli.ps1 engine run-autonomous --candidate C:\private\candidate.json --qualification C:\private\qualification.json --authorization C:\private\authorization.json --earnings-database "$env:LOCALAPPDATA\GRANDEAlpha\earnings.db" --source C:\private\current-research.json --connect
```

`run-autonomous` validates the exact certificate and permit, connects to the selected Agentic
account, checks the pinned provider contract, and requires an exact terminal phrase for initial
arming. After a crash, it may recover only the sole active, unexpired grant with the same digest.
A deliberate stop revokes authority; it does not cancel an accepted order or close a position.
The snapshot file is reloaded each cycle and must be atomically refreshed by a qualified data
collector. Preserve the audit and execution databases for reconciliation.

Authorization can occur while the market is closed. The runner waits through closed sessions,
but the current stock-capable route creates only regular-hours market/GFD tickets. A running PC
does not extend authority or guarantee uptime. A loss threshold blocks new entries after its
specified observation; gaps, fills, and outages can exceed it.

Before operation, an operator must select the exact account, symbols, capital and order/exposure/
loss limits, grant interval, overnight-holding policy, host, and local response procedure. These
are user decisions, not inferred defaults. Engineering must qualify the chosen data, strategy,
broker contract, and host under the [production qualification contract](PRODUCTION_QUALIFICATION.md).
The [standing engine](UNATTENDED_ENGINE.md) describes the separate older ETF route. Neither path
is a profitability claim or a substitute for professional advice where required.

## Supervised experimental real-order mode

GRANDE Alpha has a distinct, opt-in supervised experimental mode for small, attended real-money
experiments. It is **not autonomous**, does not claim `LIVE_REVIEW_ELIGIBLE`, does not bypass or
satisfy the autonomous evidence gate, and does not imply that the selected strategy is profitable.

The capability defaults off. Enabling it in **Settings & Permissions** requires Robinhood broker
access, the exact `ENABLE LIVE ORDERS` phrase, and the constrained Regular market / Market order /
GFD / cash-T+1 route. Enabling the capability does not create authority or place an order.

### Hard session boundary

A separate supervised session must be created while the regular-session entry window is open. The
session binds the active Agentic account, TQQQ and SQQQ, the exact strategy fingerprint, same-day
expiry, and all existing risk checks. Supervised mode adds hard upper bounds that the dialog cannot
raise and the controller independently rechecks:

- $10 maximum notional per order;
- $50 maximum gross submitted notional per Eastern trading day; and
- $40 maximum total TQQQ/SQQQ exposure.

Lower user-selected limits still apply. Spread, quote age, session loss, order-rate, order-count,
buying-power, inventory, settlement, and reconciliation gates remain independent and fail closed.

### One confirmation per order

For every strategy-generated buy or sell, the controller first obtains Robinhood's exact review.
The desktop then presents the bound Agentic account, ticker, side, dollar amount or share quantity,
order type, market-hours route, time in force, limit price when applicable, reviewed bid and ask,
venue quote time, estimated execution price/notional, strategy reason, and Robinhood's market-data
disclosure verbatim.

The user must type the ticket-specific phrase. The resulting decision is bound to the immutable
preview id, exact phrase, and confirmation time. It is one-use, never remembered, and expires after
30 seconds or at session expiry. After confirmation, GRANDE Alpha refreshes exact broker account,
position, order, and bid/ask truth and reruns risk authorization. Account/authority changes, stale or
misaligned venue clocks, a new open order, inventory changes, expired consent, or material reviewed
price movement reject the ticket without placement and require a fresh review.

Declining a normal ticket places nothing and leaves the bounded session available for later strategy
decisions. A missing or failed confirmation UI revokes authority. A loss-limit exit also requires
per-order confirmation; if it is declined or unavailable, the app locks and directs the user to
verify and flatten manually.

Only a consumed, valid one-use decision can reach the provider placement call. Review request,
decision, consumption, placement acknowledgement, and later broker reconciliation are recorded as
separate audit receipts.

### What remains locked

GRANDE Alpha installs no scheduler and cannot run supervised mode unattended. The original
`authorize_live` evidence-gated path remains separate and continues to require an exact current
evidence certificate and every runtime-parity gate. Supervised experiments are not evidence that an
autonomous strategy is safe, profitable, or suitable for a particular person, account, or
jurisdiction. Obtain individualized legal, tax, employment, residency, and account-eligibility
guidance before using real money.
