# Safety and compliance boundaries

This is general product documentation, not investment, legal, accounting, or tax advice.

## Research and live authority

Sandbox mode uses fictional aliases and no broker connection. Shadow mode reads current provider data but records fictional fills. Neither can grant live authority. Broker access and real-order automation are separate opt-ins, and live authority expires each session.

Saved capability opt-in is not money-moving consent. Every live session starts locked and requires a
new typed, exact-scope grant for one account, ticker set, route, strategy fingerprint, Eastern day,
and numeric risk envelope. The grant exists only in memory and cannot be remembered across restart.
Active sessions expose separate pause and revoke actions and emit hash-chained action receipts. See
[Bounded autonomous authority](AUTONOMOUS_AUTHORITY.md) for the enforcement and integration contract.
The [live-pilot activation guide](LIVE_ACTIVATION.md) records the current CASH/evidence/OAuth stop
state, regular-hours/GFD-only route, ambiguous-order quarantine, and external release gates.

The optional Robinhood adapter may receive read access to account numbers, balances, positions,
transactions, orders, watchlists, and scans across connected accounts. Robinhood's public overview
states that order placement is restricted to the dedicated Agentic account, that an agent may place
orders without confirmation when instructed, and that agentic trading can result in total loss.
However, the current order-review tool contract separately requires the exact preview and disclosure
to be presented for explicit confirmation before placement. GRANDE Alpha applies the stricter current
tool contract: its session grant is not treated as per-order confirmation. The distinct
[supervised experimental mode](SUPERVISED_EXPERIMENTAL.md) requires a fresh, transaction-bound typed
decision for every reviewed order; the autonomous path remains evidence/parity blocked. Review the
provider's current [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
before connecting.

The application further restricts its own behavior, but cannot control provider availability, settlement, fills, market gaps, account restrictions, or a compromised host.

## Leveraged and inverse ETFs

TQQQ and SQQQ seek leveraged or inverse **daily** objectives. Compounding means results over periods longer than a day can differ substantially from a simple multiple of the index return. Volatility, leverage, derivatives, financing, concentration, correlation, and liquidity can amplify loss. Review each fund's current prospectus and sponsor disclosures.

## Account and trading rules

Account type, settled funds, buying power, day-trading rules, good-faith violations, freeriding, market hours, and order eligibility remain broker and jurisdiction dependent. Do not encode a social-media claim about rule changes as product logic. The application trusts current provider responses and fails closed on warnings; it does not determine whether a trade is lawful or suitable.

Regular, extended, and 24 Hour Market selections have different liquidity, volatility, spread,
fractional-share, and order-type constraints. Extended and overnight automation uses whole-share
limits; a limit is not guaranteed to fill. GTC orders can remain live at the broker after the desktop
app exits. Review the exact behavior in [Trading sessions and order routes](TRADING_SESSIONS.md).

## Jurisdiction, account, employment, and business status

Users with visa, residency, employment-authorization, sanctions, cross-border, or business-classification questions must obtain advice applicable to their facts before enabling automation. The application attestation is a consent checkpoint, not legal clearance. Never use it to manage another person's money, accept outside capital, sell managed-account services, or evade a restriction.

GRANDE Alpha deliberately presents this as an outside-app responsibility rather than a pass/fail
condition it could falsely certify. It does not collect or infer citizenship, visa, employer,
tax-residency, professional-status, or business-formation details. Each user must consult the broker,
relevant authorities, and appropriately qualified professionals for their exact circumstances.
Distributors can configure official HTTPS reference links as described in
[Activation checklist](ACTIVATION_CHECKLIST.md); a configured link never becomes app-issued approval.

Public distribution with Robinhood connectivity is also blocked pending written Robinhood approval
covering the exact product/API and distribution model. Review Robinhood's
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/)
and [legal library](https://robinhood.com/us/en/legal/). Do not imply Robinhood endorsement.

## Taxes and records

Do not assume a refund or balance due. Filing status, residency, treaties, withholding, income, gains, losses, wash-sale treatment, estimated payments, and deadlines require source documents and applicable professional guidance.

Retain broker confirmations, consolidated tax forms, transfers, fills, fees, tax lots, elections, and records for substantially identical holdings in other accounts. Broker and custodian records—not the app's estimated P/L—are authoritative. The app does not file returns or calculate tax liability.

## Stops and emergency control

The red **STOP + CANCEL** control first blocks new local requests, refreshes broker truth, and presents
a blocking preview of the exact nonterminal Agentic-account orders owned by GRANDE Alpha's durable
intent ledger. Cancellation requires explicit confirmation of that count and scope. Unrelated or
manually placed orders are never included. Orders already pending cancellation are disclosed and
verified without a duplicate cancellation request.

Revoke, permission disablement, Disconnect, credential forgetting, Exit, and internal fault handling
do not cancel implicitly. They lock or refuse while GRANDE-owned open or unresolved order state
remains and direct the operator to **STOP + CANCEL**. Even a confirmed cancellation can race a fill
and cannot be guaranteed during a provider, network, operating-system, or power failure. The app
therefore remains connected/open when cleanup is unresolved. Local stop-loss and take-profit
decisions cannot execute while the app is unavailable, and a filled position remains the user's
responsibility until a broker confirms its sale.

The local market calendar covers scheduled recurring holidays and early closes, not emergency
closures, venue outages, or trading halts. Use the official
[NYSE calendar](https://www.nyse.com/markets/hours-calendars) and
[Nasdaq halt page](https://www.nasdaqtrader.com/trader.aspx?id=tradehalts), and treat broker/venue
state as authoritative.
