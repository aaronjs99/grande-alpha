# Safety and compliance boundaries

This is general product documentation, not investment, legal, immigration, accounting, or tax advice.

## Research and live authority

Sandbox mode uses fictional aliases and no broker connection. Shadow mode reads current provider data but records fictional fills. Neither can grant live authority. Broker access and real-order automation are separate opt-ins, and live authority expires each session.

The optional Robinhood adapter may receive read access to account numbers, balances, positions, transactions, orders, watchlists, and scans across connected accounts. Robinhood states that order placement is restricted to the dedicated Agentic account, that automated trades may occur without per-transaction confirmation if authorized, and that agentic trading can result in total loss. Review the provider's current [Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) before connecting.

The application further restricts its own behavior, but cannot control provider availability, settlement, fills, market gaps, account restrictions, or a compromised host.

## Leveraged and inverse ETFs

TQQQ and SQQQ seek leveraged or inverse **daily** objectives. Compounding means results over periods longer than a day can differ substantially from a simple multiple of the index return. Volatility, leverage, derivatives, financing, concentration, correlation, and liquidity can amplify loss. Review each fund's current prospectus and sponsor disclosures.

## Account and trading rules

Account type, settled funds, buying power, day-trading rules, good-faith violations, freeriding, market hours, and order eligibility remain broker and jurisdiction dependent. Do not encode a social-media claim about rule changes as product logic. The application trusts current provider responses and fails closed on warnings; it does not determine whether a trade is lawful or suitable.

## International, student, employment, and business status

Users with visa, residency, employment-authorization, sanctions, cross-border, or business-classification questions must obtain advice applicable to their facts before enabling automation. The application attestation is a consent checkpoint, not legal clearance. Never use it to manage another person's money, accept outside capital, sell managed-account services, or evade a restriction.

## Taxes and records

Do not assume a refund or balance due. Filing status, residency, treaties, withholding, income, gains, losses, wash-sale treatment, estimated payments, and deadlines require source documents and applicable professional guidance.

Retain broker confirmations, consolidated tax forms, transfers, fills, fees, tax lots, elections, and records for substantially identical holdings in other accounts. Broker and custodian records—not the app's estimated P/L—are authoritative. The app does not file returns or calculate tax liability.

## Stops and emergency control

The red stop control blocks new local requests and attempts cancellation. A cancellation can race a fill and cannot be guaranteed during a provider, network, operating-system, or power failure. Local stop-loss and take-profit decisions cannot execute while the app is unavailable. A filled position remains the user's responsibility until a broker confirms its sale.
