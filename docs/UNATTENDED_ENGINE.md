# Bounded unattended engine

The older ETF standing runner and newer mixed stock/ETF runner are separate paths. Both require
exact qualification and broker checks; neither is provider-observed or deployment-qualified by
source implementation alone. A Windows PC can host a foreground runner without a GUI or installed
scheduler, but the operator owns its uptime and recovery.

## Authority and broker compatibility

`engine run-unattended` is separate from the attended `engine run-live` route. It requires current
exact strategy evidence, every grant limit, explicit standing terms, fresh account/quote preflight,
and a typed session approval in an interactive terminal. Supervised experimental exemptions cannot
be used for unattended operation. The empty template grants nothing.

The session approves the exact candidate's sizing within the supplied grant limits, including
strategy sells and loss exits. It expressly skips broker review and per-ticket confirmation.
It does not pretend that a person approved each ticket or that a local quote check is a broker review.

Robinhood's placement metadata observed on 2026-09-07 permits explicit review bypass; its cancellation
metadata still requires user confirmation. The engine pins the full observed tool-inventory digest
and official server URL. An unfamiliar connection contract fails closed and requires engineering
review; editing a policy cannot approve a new provider contract. Metadata is checked from the
connected adapter's discovery snapshot, not continuously rediscovered from the server.

Automatic cancellation is deliberately unsupported. Stopping cannot undo a request already sent.
This is not a guarantee of exact-once broker execution or a guarantee that a loss threshold will
cap realized losses. Broker outages, price gaps, partial fills and unclosed positions remain risks.

## Prepare and validate; do not activate an unqualified candidate

```powershell
.\grande.ps1 cli engine standing-template
.\grande.ps1 cli engine policy-check --unattended --policy C:\private\standing-policy.json
```

The standing policy extends the attended policy with this explicit `standing` object:

```json
{
  "skip_broker_review_and_per_order_confirmation": true,
  "sizing": "exact_candidate_within_grant_limits",
  "allow_strategy_sells_and_loss_exits": true,
  "automatic_cancellation": false,
  "restart": "recover_same_unexpired_unchanged_authorization",
  "stop": "revoke_new_orders_residual_exposure_may_remain"
}
```

All other fields follow [the session policy schema](CLI.md). No account, money amount, strategy,
or duration is chosen automatically. After strategy qualification and operator approval, activation is:

```powershell
.\grande.ps1 cli engine run-unattended --policy C:\private\standing-policy.json --connect
```

Local OAuth and the typed `SKIP REVIEW AND ARM` phrase must be completed by the operator. Session
authority expires within the same Eastern trading day, at most 360 minutes. Staying awake does not
renew it. This older ETF route does not promise indefinite, overnight or multi-day operation.
The separate mixed route has a bounded multi-day scope; see [live activation](LIVE_ACTIVATION.md).

## Stop and recovery

In a second terminal on the same PC and Windows user/data directory:

```powershell
.\grande.ps1 cli engine stop
```

This records revocation for all local standing sessions without taking the runner's instance lock
or contacting the broker. The runner checks revocation before placement and polls it approximately
every 100 ms while awaiting broker responses. This is not a hard real-time bound: a frozen process,
OS or storage device can delay observation. Ctrl+C also stops the foreground session. Neither
control requests cancellation or liquidation. Verify open orders and exposure in Robinhood.

Each logical order is durably recorded with its original reference before placement. Counts and
loss-stop history survive a restart. Ambiguous placement outcomes are not retried. The mixed engine
can recover only the same unexpired, unchanged durable authority after obtaining the exact account
lease; stopped, changed and expired authorities remain unusable. Missing or unreadable stop/risk
state fails closed. Keep the audit and execution databases intact.

The runner never resumes itself after a crash. There is no installed scheduler. Delivery of alerts,
native-host outage testing, and provider-observed partial/fill/cancellation recovery qualification
remain separate work. Historical offline checks injected fabricated evidence readiness solely to
exercise engine mechanics. Automated tests exist, but they do not substitute for provider-observed
qualification. Production evidence gates are unchanged.
See [mixed production qualification](PRODUCTION_QUALIFICATION.md) for the newer stock/ETF path.
The execution change advances the evidence policy to version 15; older evidence cannot silently
qualify this new order path.

## Legacy desktop autonomous authority

GRANDE Alpha starts with real-order capability disabled. Enabling that capability in Settings does
not authorize money movement. A separate, in-memory authority must be created for each bounded live
session. No session grant, confirmation phrase, paused state, or money-moving authority is written to
configuration or restored after restart.

These controls limit authority; they do not make a strategy profitable, suitable, lawful, or likely
to fill. Shadow mode remains the default and submits no orders.

The controller integration was introduced in v0.15 and is retained in the current release. That is
an engineering statement, not deployment approval. See [Live-pilot activation and external
gates](LIVE_ACTIVATION.md) for the current CASH/evidence/OAuth stop state and operating procedure.

### Exact session scope

Each authority is immutable and binds all of the following:

- one exact Agentic account number;
- an explicit nonempty subset of `TQQQ` and `SQQQ`;
- one exact SHA-256 strategy/cadence/execution fingerprint;
- one market-hours route, order type, time in force, and limit-price offset;
- an ET start and expiry on the same Eastern calendar day; and
- per-order notional, gross daily notional, total exposure, session loss, order count,
  orders-per-minute, spread, and quote-age ceilings.

The authorization dialog displays that scope and requires a phrase containing the account suffix,
fingerprint prefix, tickers, and route. The phrase is a one-time consent check and is never retained.
Changing a bound setting requires revocation and a new authority; it is not silently broadened.

### Runtime enforcement

The independent risk engine fails closed unless the controller supplies the same current account and
full strategy fingerprint on every authorization check. It checks ticker, route, market window,
quote symbol, quote age, spread, order and daily gross notional, exposure, broker-reported buying
power, session loss, order count, rate, and idempotency key.

An allowed intent reserves its gross notional before broker review. A submitted order converts that
reservation into used daily notional. The placement invocation is counted conservatively even when
the broker response is ambiguous. A failed or abandoned review before placement must release the reservation.
Buys and sells both count toward the gross daily-notional and order-count caps. Every autonomous sell
is one-shot: authority is revoked immediately after a known placement response, no automatic retry is
allowed, and the user must verify broker inventory before granting new authority. This prevents a
terminal fill racing a stale positions snapshot from causing a duplicate exit. The loss ceiling blocks
new entries while preserving the ability to request one risk-reducing sell within every other bound;
that loss-limit sell follows the same one-shot rule.

### Pause and revoke

An active authority surface must keep **Pause authority** and **Revoke authority** visible:

- Pause immediately blocks every new local authorization while preserving the same expiring grant.
- Resume is an explicit user action and cannot extend or change the grant.
- Revoke destroys the in-memory grant and its outstanding reservations. Resuming then requires a new
  typed confirmation and new grant.
- Expiry fails closed. Restarting the application returns to `LOCKED` even if capability remains
  enabled in Settings.

Pause and Revoke only block local authority; neither cancels an order or a fill already accepted by
the broker. The separate **STOP + CANCEL** path first previews the exact GRANDE-owned nonterminal
Agentic orders and requires explicit confirmation. Manual/unrelated orders remain untouched, and an
already-pending cancellation is disclosed and verified without a duplicate request. The broker
remains authoritative.

### Immutable action receipts

Authority creation, pause, resume, revoke, expiry, authorization decisions, reservation release, and
recorded submissions emit frozen `AuthorityActionReceipt` values. Each receipt includes the authority
scope digest and the preceding receipt digest, creating a tamper-evident hash chain. Receipt payloads
never contain the typed phrase.

The risk engine only queues these receipts in memory. The controller must drain them promptly and
append their dictionaries to the existing receipt store. Storage must never overwrite or update a
receipt; diagnostics must continue to redact account identifiers. Persistence of receipts is audit
evidence, not persistence of authority.

Revocation, re-authorization, and application restart must not reset the day's spend or order-count
budgets. When arming, the controller restores the same-ET-day gross placement-invocation notional and
order count from append-only receipts. It also supplies the preceding receipt digest so the chain can
continue across restart. Restored usage is validated and cannot exceed the new grant's caps.

### Integrated controller contract

The live-pilot controller:

1. construct the dialog with the exact current strategy fingerprint;
2. pass the current account number and full fingerprint to every `RiskEngine.authorize` call;
3. restore same-ET-day usage and the prior receipt digest before arming, then drain and append receipts
   after arm, decision, pause/resume/revoke, release, placement invocation, and expiry;
4. call `release_authorization(ref_id)` whenever review is abandoned before placement, and count a
   placement invocation even when its outcome is ambiguous;
5. wire the visible authority panel to pause, resume, and revoke actions; and
6. preserve all existing evidence, settlement, broker-review, cancellation, and default-disabled gates.

The UI exposes one explicit **Authorize & Start Live Session** action per bounded same-ET-day grant.
No grant is restored after restart, and no scheduler is installed. A missing account/fingerprint
context rejects the order. The authority model, risk engine, and UI components themselves perform no
broker write; only the separately gated controller can cross the broker review/placement boundary.

An ambiguous placement acknowledgement is never retried. It is durably quarantined, authority is
revoked, and broker reconciliation is required. Revoke, Settings, Disconnect, and Exit do not cancel
it. A separately confirmed **STOP + CANCEL** action is not treated as complete until every exact
target order is observed terminal; unresolved cleanup keeps the desktop app connected and open.
