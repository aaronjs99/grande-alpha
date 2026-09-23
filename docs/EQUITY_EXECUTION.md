# Stock execution primitives

Status: implemented primitives tested with fabricated data, **not wired into live strategy dispatch**.
This work does not activate individual-stock trading or qualify multi-day autonomous operation.

## Implemented

- `EquityOrderIntent` supports stock ticker syntax and the existing broker argument shape, restricted
  to regular-hours market/GFD. UUID references, cent-sized dollar amounts and six-decimal quantities
  are exact; serialization cannot silently change amounts. Sells require share quantities.
- `EquityScope` validates explicit account/universe/caps and a bounded interval of up to seven days.
  It is a parameter object, not user consent, strategy qualification or an activated standing grant.
- `assess_ticket` checks every held asset's quote and exposure, account identity, cash/eligibility,
  snapshot age, unresolved orders, daily usage, loss-stop status and inventory-backed sells. It never
  grants authority. It requires cash-account truth; caller-supplied eligibility and usage must come
  from qualified current sources when integrated. Estimated notional is not a guaranteed fill cost.
- `EquityLedger` keeps immutable provider executions in separate `equity_v1_*` tables, leaving legacy
  ETF evidence untouched. One outstanding intent per account prevents overlapping unresolved writes.
  The original reference is committed before dispatch, and attempted/unknown tickets cannot reset.
  Repeated snapshots deduplicate fills; altered IDs, dropped fills, amount/route/account mismatches,
  and terminal-to-open regressions fail closed. An unbound broker order needs an exact echoed reference.
  Symbol/price/time similarity never substitutes for identity.

No live runner, GUI or automatic policy loader uses this new scope yet. The existing `OrderIntent`
and live grant retain the TQQQ/SQQQ restriction and same-day expiry. A stock-capable primitive is not
permission for an old session to change assets.

## Required integration work

The combined strategy still needs a dedicated dispatcher binding its approved policy, exact candidate
evidence, provider metadata and fresh instrument eligibility to these primitives. Daily usage and
loss state must feed the risk check from durable account truth; its boolean inputs are not substitutes
for that integration. Portfolio allocation/sector/gross-proxy limits must be rechecked before every
dispatch, not only when a research target is calculated.

Also unfinished: startup reconciliation of existing stock holdings, multi-day authority/stop UI,
current earnings data ingestion, corporate-action handling, and end-to-end provider qualification.
Do not widen the legacy ETF allowlist or delete databases to make those checks disappear.
