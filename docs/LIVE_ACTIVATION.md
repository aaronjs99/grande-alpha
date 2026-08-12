# Live-pilot activation and external gates

The v0.15 live-pilot path is integrated, but it is **not currently eligible for directional live
trading**. Integration means the safety workflow exists and is testable; it does not mean a strategy
has positive evidence, legal clearance, provider approval for distribution, or a profit expectation.

The current Robinhood order-review tool contract requires the exact reviewed ticket and market
disclosure to be presented for explicit confirmation before each placement. The app's same-day
bounded grant does not satisfy that later per-order confirmation. Autonomous placement therefore
remains machine-blocked unless a compliant per-order confirmation flow is implemented or Robinhood
provides written clarification that expressly covers this exact session-authority design.

## Current stop state

As of August 11, 2026:

- the installed runtime defaults to deterministic **CASH / hold**, which requests no TQQQ or SQQQ
  position;
- `RUNTIME_SIZING_PARITY_CERTIFIED` is `false`, so a non-cash candidate cannot obtain a valid
  directional live certificate;
- the sealed final holdout has not established a positive deployable result, and monitored forward
  shadow evidence is still required; and
- the latest local noninteractive regular-session check authenticated to exactly one active Agentic
  cash account, found no real positions or nonterminal orders, and passed the strict exact-symbol,
  venue-timestamp freshness, and batch-skew checks for QQQ/TQQQ/SQQQ. The structural read-only
  boundary reported zero broker write calls. Re-run this check at every future activation time.

Therefore, do not represent the app as profitable, live-ready, or approved to trade directionally.
The correct runtime decision remains CASH or shadow-only research. A directional candidate must first
pass the development gates, the one-use final holdout, sizing parity, and a monitored forward-shadow
period with positive after-cost evidence. Passing those gates would permit a separate live review; it
would not guarantee future profit.

Activation evidence must use policy v13, runtime-observation schema v2, quote-batch schema v2,
and exact quote validator v2. Policy-v12 receipts and validator-v1 traces predate durable bid/ask
book clocks and are intentionally stale; they cannot unlock live review.

## If the local OAuth session is revoked

The latest check authenticated successfully. If a future check reports that the credential was
revoked, do not repeatedly retry it.

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

One deliberate **Authorize & Start Live Session** action inside the regular-session entry window
creates and starts one bounded authority for the same Eastern calendar day. The live signal pipeline
is reset at that moment, so premarket and pre-start observations cannot warm the candidate. The grant
binds exactly one active Robinhood Agentic account, both
TQQQ and SQQQ, the current strategy fingerprint, the regular-hours/GFD route, expiry, and numeric
limits. The grant and typed phrase are never persisted. Restart, expiry, revocation, account change,
or fingerprint change returns the app to `LOCKED` and requires a new explicit authorize-and-start.
Pause/resume may continue the same still-valid grant but cannot extend or alter it.

There is **no automatic live schedule**. The optional Windows scheduled task starts read-only live
shadow only and cannot authorize, review, place, or cancel orders.

Before authority is created—and again before autonomous start—the app requires:

- the exact connected Agentic account to be active and freshly reconciled;
- zero real TQQQ/SQQQ position and zero nonterminal Agentic orders;
- no durable or in-memory unresolved placement outcome;
- positive broker-reported account value and buying power;
- exact QQQ, TQQQ, and SQQQ quotes, with matching symbols, valid prices, bounded timestamp skew, and
  age within the grant's quote-age cap; and
- a current evidence certificate for the exact candidate, cadence, route, settlement/sizing contract,
  and requested risk envelope.

Any failed preflight leaves real-order automation locked.

## Daily budgets survive restart

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

## F-1 and public-product release gates

For this user's F-1 circumstances, live automated trading and commercialization remain externally
blocked until both the UCLA Designated School Official and qualified U.S. immigration counsel review
the exact facts and provide applicable guidance. The in-app attestation is not immigration clearance.
Start with the UCLA Dashew Center's
[F-1 counselor contact page](https://internationalcenter.ucla.edu/contact-us) and the federal
[SEVP employment guidance](https://www.ice.gov/sevis/employment). Do not infer that personal trading,
software development, selling software, subscriptions, managed accounts, or other monetization share
the same immigration treatment.

Before distributing a public product that connects to Robinhood or exposing a Robinhood-backed API,
obtain written Robinhood approval that expressly covers the intended product, users, order flow,
branding, data handling, and distribution model. Robinhood's official
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/)
states that trading APIs may not be linked without written authorization; also review the current
[Robinhood legal library](https://robinhood.com/us/en/legal/). Until written scope is obtained, a
public release must not offer Robinhood connectivity or imply Robinhood approval, partnership, or
endorsement.
