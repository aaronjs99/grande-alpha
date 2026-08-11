# Safety and compliance boundaries

This is general product documentation, not investment, legal, immigration, accounting, or tax advice.

## Research and live authority

Sandbox mode uses fictional aliases and no broker connection. Shadow mode reads current provider data but records fictional fills. Neither can grant live authority. Broker access and real-order automation are separate opt-ins, and live authority expires each session.

Saved capability opt-in is not money-moving consent. Every live session starts locked and requires a
new typed, exact-scope grant for one account, ticker set, route, strategy fingerprint, Eastern day,
and numeric risk envelope. The grant exists only in memory and cannot be remembered across restart.
Active sessions expose separate pause and revoke actions and emit hash-chained action receipts. See
[Bounded autonomous authority](AUTONOMOUS_AUTHORITY.md) for the enforcement and integration contract.
The [live-pilot activation guide](LIVE_ACTIVATION.md) records the current CASH/evidence/OAuth stop
state, regular-hours/GFD-only route, ambiguous-order quarantine, and external release gates.

The optional Robinhood adapter may receive read access to account numbers, balances, positions, transactions, orders, watchlists, and scans across connected accounts. Robinhood states that order placement is restricted to the dedicated Agentic account, that automated trades may occur without per-transaction confirmation if authorized, and that agentic trading can result in total loss. Review the provider's current [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) before connecting.

The application further restricts its own behavior, but cannot control provider availability, settlement, fills, market gaps, account restrictions, or a compromised host.

## Leveraged and inverse ETFs

TQQQ and SQQQ seek leveraged or inverse **daily** objectives. Compounding means results over periods longer than a day can differ substantially from a simple multiple of the index return. Volatility, leverage, derivatives, financing, concentration, correlation, and liquidity can amplify loss. Review each fund's current prospectus and sponsor disclosures.

## Account and trading rules

Account type, settled funds, buying power, day-trading rules, good-faith violations, freeriding, market hours, and order eligibility remain broker and jurisdiction dependent. Do not encode a social-media claim about rule changes as product logic. The application trusts current provider responses and fails closed on warnings; it does not determine whether a trade is lawful or suitable.

Regular, extended, and 24 Hour Market selections have different liquidity, volatility, spread,
fractional-share, and order-type constraints. Extended and overnight automation uses whole-share
limits; a limit is not guaranteed to fill. GTC orders can remain live at the broker after the desktop
app exits. Review the exact behavior in [Trading sessions and order routes](TRADING_SESSIONS.md).

## International, student, employment, and business status

Users with visa, residency, employment-authorization, sanctions, cross-border, or business-classification questions must obtain advice applicable to their facts before enabling automation. The application attestation is a consent checkpoint, not legal clearance. Never use it to manage another person's money, accept outside capital, sell managed-account services, or evade a restriction.

For the current F-1 user, both a UCLA DSO/F-1 counselor and qualified immigration counsel are an
external gate before live automation or commercialization. UCLA directs status-specific questions to
its [Dashew Center F-1 counselors](https://internationalcenter.ucla.edu/contact-us); federal
[SEVP employment guidance](https://www.ice.gov/sevis/employment) explains that employment categories
have specific authorization rules. Neither source makes the app capable of deciding whether a
particular trading or software-business activity is permitted.

Public distribution with Robinhood connectivity is also blocked pending written Robinhood approval
covering the exact product/API and distribution model. Review Robinhood's
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/)
and [legal library](https://robinhood.com/us/en/legal/). Do not imply Robinhood endorsement.

## Taxes and records

Do not assume a refund or balance due. Filing status, residency, treaties, withholding, income, gains, losses, wash-sale treatment, estimated payments, and deadlines require source documents and applicable professional guidance.

Retain broker confirmations, consolidated tax forms, transfers, fills, fees, tax lots, elections, and records for substantially identical holdings in other accounts. Broker and custodian records—not the app's estimated P/L—are authoritative. The app does not file returns or calculate tax liability.

## Stops and emergency control

The red stop control blocks new local requests, attempts cancellation, and checks the broker for a
terminal result. A cancellation can race a fill and cannot be guaranteed during a provider, network,
operating-system, or power failure. If cleanup remains unresolved, the connected app refuses a clean
exit; check Robinhood and retry stop/cancel. Local stop-loss and take-profit decisions cannot execute
while the app is unavailable. A filled position remains the user's responsibility until a broker
confirms its sale.

The local market calendar covers scheduled recurring holidays and early closes, not emergency
closures, venue outages, or trading halts. Use the official
[NYSE calendar](https://www.nyse.com/markets/hours-calendars) and
[Nasdaq halt page](https://www.nasdaqtrader.com/trader.aspx?id=tradehalts), and treat broker/venue
state as authoritative.
