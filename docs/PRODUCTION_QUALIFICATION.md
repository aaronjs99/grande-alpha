# Mixed stock/ETF production qualification

The mixed PEAD plus TQQQ/SQQQ path is implemented behind a closed production gate. It is not
qualified merely because the code imports, an offline test passes, or a policy file exists. Creating
the production engine grants no authority and sends no order.

Qualification and consent are separate. The evidence certificate says that an exact candidate met
the engineering gates. A second account-bound authorization permit records the user's exact scope,
terms and expiry. Both are rechecked before arm, every cycle, and restart recovery. The permit lasts
at most seven days and never renews itself.

## Runtime chain

1. `AlphaVantageEarningsClient` captures raw `EARNINGS` and `EARNINGS_ESTIMATES` responses with an
   observation time and content hash. `EarningsObservationStore` never rewrites those observations.
2. Normalized actual and consensus facts remain linked to the exact raw response. A consensus must
   have been captured before the announcement; a post-announcement estimate cannot be relabeled.
3. Every execution cycle validates the event's exact actual/consensus values, period, basis,
   currency, symbol and availability chronology against those stored facts.
4. `VerifiedBrokerEligibility` checks the pinned Robinhood MCP contract, exact account-symbol
   tradability, and the exact proposed ticket through broker review before risk assessment.
5. The deterministic risk layer independently checks the account, cash, inventory, quotes, spread,
   loss latch, exposure, daily notional, daily count and per-minute order rate.
6. The durable ledger reserves one UUID intent before dispatch. One unresolved intent and one live
   account lease are permitted. An ambiguous response becomes `unknown` and is never retried.

## Evidence gate

The exact candidate fingerprint must have a current certificate containing:

- at least 100 chronological historical frames with positive net change after configured costs;
- at least 100 append-only forward decisions across at least 20 sessions, captured within the
  recorder's observation-time limit and positive after configured costs;
- nonnegative drawdown measurements and content hashes for both evidence inputs;
- passing stop, daily-loss, duplicate-process, unknown-order and restart-recovery checks; and
- the pinned broker contract hash.

The certificate is valid for at most 30 days. It does not prove future profit, prevent market gaps,
or replace broker records. The current thresholds are conservative release gates, not a statistical
claim that 100 observations are sufficient for every strategy.

## Stop semantics

`stop()` durably revokes new local orders and releases the process lease immediately. `shutdown()`
can additionally request cancellation only for order IDs already bound to this engine's ledger and
then verifies whether any remain open. Broker cancellation is a separate money-moving action and the
currently pinned provider contract requires user confirmation, so the unattended loop does not call
it automatically. A stopped process may therefore leave an accepted order or filled position.

## Operator entry point

`engine autonomous-template` emits the strict candidate schema. `engine autonomous-readiness`
validates the candidate, evidence certificate, account-bound authorization, earnings store and a
fresh research snapshot without contacting the broker. `engine run-autonomous` performs the final
provider/account checks and starts the foreground runner after an exact activation phrase.

The first authorization may occur while markets are closed. During closed sessions the runner only
maintains and checks its local authority and process lease; it does not load a strategy snapshot,
review a ticket or submit an order. After an abnormal process loss, the runner can recover the one
unchanged active authority for the exact candidate. Multiple authorities, an expired permit, a
changed candidate or another live account lease all fail closed.

## What still blocks activation

Activation stays closed until the operator supplies an Alpha Vantage key through a secret store,
captures licensed point-in-time data, produces passing historical and forward reports for the exact
candidate, and completes provider-observed cancellation/fill/outage checks. The current repository
contains the engine, operator runner and offline test harness, not those missing real-world
observations or an always-on deployment qualification.
