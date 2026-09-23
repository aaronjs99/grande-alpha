# Crypto broker contract

The adapter follows the tool definitions exported from the connected Robinhood Trading MCP
server on September 23, 2026. The export is not committed: repository fixtures contain synthetic
identifiers and values only. No broker was authenticated or called during development verification.

`broker/crypto.py` provides isolated positions, orders, preview, placement, and cancellation
primitives. The [managed execution journal](AGENT_EXECUTION_JOURNAL.md) now wraps submission and
recovery with durable references and shared capital reservations. **Agent analysis and budget
controls cannot invoke order mutations.** Live runtime authority remains disconnected.

## Account and instrument mapping

| Value | Meaning and use |
| --- | --- |
| `account_number` | Selected active Agentic brokerage account; portfolio reads |
| `rhs_account_number` | Numeric identifier required by crypto tools and account-routed quotes |
| `rhc_account_number` | Linked crypto account display identifier; checked against optional response echoes |
| `account_id` | UUID on crypto position/order rows; pinned consistently across scoped responses, never converted to another identifier |
| Pair `id`, `symbol` | Provider pair identity and canonical `BTC-USD` form |
| Quote `id`, `symbol` | Same pair identity plus compact `BTCUSD` form; both must match |

The adapter refreshes account eligibility before each operation and requires the uniquely
selected active Agentic account with a numeric RHS identifier and linked crypto account.
Orders verify the RHS echo on every page; optional crypto account echoes must also match.
Pagination rejects repeated cursors, duplicate identities, and excessive results. Position
sellability comes from `quantity_transferable`, not total holdings. Direct-purchase cost basis
is preserved separately because transfers and rewards can leave part of the holding unpriced.

## Order primitives

- Amounts remain exact decimal strings on the wire. Quantity and dollar sizing are mutually
  exclusive. Fresh pair metadata checks size/price increments, tradability for the account type,
  market-only restrictions, display-only pairs, and reported halts.
- Market and limit orders use GTC. Stop and stop limit orders accept the broker's listed
  durations. Crypto never inherits equity market-hours fields or IOC routing. Specified-tax-lot
  selection is not implemented; the provider's default disposal applies.
- Preview validates the echoed instrument, side, type, quantity/prices, speculative status,
  provider timestamp, resolved size, linked account, and estimated fee. Buys use only reported
  crypto cash buying power with a conservative execution allowance; sells require verified
  available quantity. This affordability check is not a shared capital reservation.
- Placement requires the same in-memory review object, at most 15 seconds old, and rechecks
  account binding, pair rules, and funds. The immutable logical intent carries one UUID `ref_id`.
  Dispatch consumes the reference even if the caller stops waiting. This adapter never retries.
- Missing order responses, approval-only responses, invalid placement echoes, and transport
  failures raise `CryptoOutcomeUnknown` with the logical reference. They are not reported as
  successful orders or fills. Reconnect clears reviews without clearing consumed references.
- Cancellation first verifies an open order in the scoped account. `accepted=True` acknowledges
  the request only; callers must read the resulting order state. There is no blanket cancellation.

The exported schema permits omission of some proof fields. A missing explicit speculative
flag or placement reference therefore blocks a preview or leaves placement unresolved; the
adapter does not manufacture proof from defaults. Unknown order states remain open.

## Fill economics

Crypto executions preserve raw price, effective price, quantity, and reported notional.
`net_rounded_executed_notional` is the definitive account debit/credit including fees and taxes.
No fee is added or subtracted from it a second time. Preview net cost is likewise already net.
Missing execution rows or net totals remain incomplete. Duplicate executions, excess filled
quantity, and contradictory cumulative totals fail validation.

## Required before autonomous integration

The adapter's own consumed-reference set and reviews still live in the broker instance.
`AgentExecutor` adds the durable pre-dispatch boundary, shared cash reservations, and read-only
restart recovery. It remains default-deny for placement. Future live integration must use that
boundary and add an explicit strategy/universe/evidence grant, managed exits/cancellation, and
market-value/unrealized-loss controls. The existing ETF authority/evidence contract does not
cover crypto. Shadow observation has no crypto order-writing path.

Verification covers synthetic advertised-schema fixtures, account binding, decimal precision,
route/size restrictions, stale and invalidated previews, changing balances/halts, uncertain
placement, partial fills, account mismatches, asynchronous cancellations, and read-only boundaries.
