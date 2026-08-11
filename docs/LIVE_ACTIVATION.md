# Live-pilot activation and external gates

The v0.15 live-pilot path is integrated, but it is **not currently eligible for directional live
trading**. Integration means the safety workflow exists and is testable; it does not mean a strategy
has positive evidence, legal clearance, provider approval for distribution, or a profit expectation.

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

## If the local OAuth session is revoked

The latest check authenticated successfully. If a future check reports that the credential was
revoked, do not repeatedly retry it.

1. Open GRANDE Alpha and select **Broker → Forget Stored OAuth Credentials…**.
2. Confirm removal. This removes the local Windows-stored credential and disconnects the app; it does
   not itself revoke a connection inside Robinhood.
3. Select **Connect Robinhood** and complete the new browser consent on Robinhood's site.
4. Refresh and run the read-only broker diagnostic. Confirm exactly one active Agentic account and
   current balances, positions, orders, and exact QQQ/TQQQ/SQQQ quotes.
5. If reconnect still fails, stop. Do not work around OAuth or copy tokens into files. Review
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

**STOP + CANCEL** first locks new local requests, requests cancellation of nonterminal Agentic
orders, and then polls broker order truth for a terminal state. A cancellation request is not proof
of cancellation: it can race a fill, fail remotely, or remain pending.

If any targeted order is missing, nonterminal, or cannot be refreshed, cleanup is unresolved. GRANDE
Alpha refuses a clean connected exit and remains open. Check Robinhood directly, retry **STOP +
CANCEL**, and close only after terminal verification passes. Filled positions are not automatically
liquidated and remain the user's responsibility.

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
