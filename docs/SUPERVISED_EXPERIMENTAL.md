# Supervised experimental real-order mode

GRANDE Alpha has a distinct, opt-in supervised experimental mode for small, attended real-money
experiments. It is **not autonomous**, does not claim `LIVE_REVIEW_ELIGIBLE`, does not bypass or
satisfy the autonomous evidence gate, and does not imply that the selected strategy is profitable.

The capability defaults off. Enabling it in **Settings & Permissions** requires Robinhood broker
access, the exact `ENABLE LIVE ORDERS` phrase, and the constrained Regular market / Market order /
GFD / cash-T+1 route. Enabling the capability does not create authority or place an order.

## Hard session boundary

A separate supervised session must be created while the regular-session entry window is open. The
session binds the active Agentic account, TQQQ and SQQQ, the exact strategy fingerprint, same-day
expiry, and all existing risk checks. Supervised mode adds hard upper bounds that the dialog cannot
raise and the controller independently rechecks:

- $10 maximum notional per order;
- $50 maximum gross submitted notional per Eastern trading day; and
- $40 maximum total TQQQ/SQQQ exposure.

Lower user-selected limits still apply. Spread, quote age, session loss, order-rate, order-count,
buying-power, inventory, settlement, and reconciliation gates remain independent and fail closed.

## One confirmation per order

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

## What remains locked

The Windows scheduled task remains read-only shadow only. It cannot authorize a supervised session,
answer a confirmation, review/place/cancel an order, or run supervised mode unattended. The original
`authorize_live` evidence-gated path remains unchanged and continues to require an exact current
evidence certificate and every runtime-parity gate. Supervised experiments are not evidence that an
autonomous strategy is safe, profitable, or suitable for a particular person, account, or
jurisdiction. Obtain individualized legal, tax, employment, residency, and account-eligibility
guidance before using real money.
