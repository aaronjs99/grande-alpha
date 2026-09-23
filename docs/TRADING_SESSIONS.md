# Trading sessions and automatic order routes

GRANDE Alpha exposes three equity-session models for research and shadow configuration. A selection
is an execution constraint, not a prediction or a reason to trade. Both current real-order pilots are
hard-locked to Regular market, Market order, GFD, and cash T+1.

| Research/shadow selection | Eastern time | Modeled order choices | Sizing |
|---|---|---|---|
| Regular market | 9:30 AM-4:00 PM | Market GFD; limit GFD/GTC | Market buys may use dollars/fractions; limits use whole shares |
| Extended market | 7:00 AM-8:00 PM | Limit GFD/GTC only | Whole shares |
| 24 Hour Market | 8:00 PM-8:00 PM on eligible trading days | Limit GFD/GTC only | Whole-share modeled sizing; no current live-pilot route |

Robinhood does not execute equity market orders during extended or overnight sessions. A market
order sent then may queue for regular open, so GRANDE Alpha does not create that combination. The
provider can also change symbol/session eligibility. Its review and placement responses remain
authoritative. See Robinhood's current [extended-hours](https://robinhood.com/us/en/support/articles/extendedhours-trading/),
[24 Hour Market](https://robinhood.com/us/en/support/articles/24hour-market/), and
[Agentic Trading](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) disclosures.

## Where the choice is made

**Settings & Permissions → Research, shadow, and live-pilot route** stores a default only. It grants
no authority. The live-session review displays the saved route read-only. In the current release,
both supervised and evidence-gated live pilots reject every route except Regular market, Market order,
GFD, and cash T+1. A resulting grant binds that exact route, account, ticker tuple, and strategy
fingerprint; it expires within the same Eastern calendar day and is never restored after restart. Any
intent that differs is rejected locally before broker review.

The sandbox exposes the broader research fields. Evidence-policy version 13 binds them and the settlement model
into the strategy fingerprint, adds a complete-session-coverage gate, and requires a one-use final
holdout. Regular data cannot certify an extended or
overnight route, and evidence does not unlock those live routes in the current release. The community adapter can request pre/post-market bars for extended research, but it
rejects 24-hour certification because it does not provide complete overnight coverage. Use a lawful,
aligned QQQ/TQQQ/SQQQ CSV with overnight timestamps and a consistent
`market_hours=all_day_hours` column for that research path. The importer requires both evening and
overnight observations and checks missing intervals across the 8:00 PM trading-date boundary.
The dataset interval must also match the live analysis interval. The sandbox includes a custom
1-300 second CSV choice; set it to 5 seconds for the default live cadence. A 1-minute run cannot
certify a 5-second strategy.

## Marketable-limit construction

For an authorized offset of `b` basis points and current quote `(bid, ask)`, the live controller uses:

```text
buy limit  = ceil_to_cent(ask × (1 + b / 10,000))
buy shares = floor(authorized notional / buy limit)

sell limit  = floor_to_cent(bid × (1 - b / 10,000))
sell shares = complete whole-share position quantity
```

The offset is a maximum price concession, not expected slippage. A limit can partially fill or never
fill. If the authorized notional cannot buy one whole share, the controller records a blocked decision
instead of exceeding the budget. If a selected limit route encounters fractional inventory, automatic
trading locks rather than leaving an opposite leveraged position on top of a fractional remainder.
The separately reviewed manual flatten remains a regular-hours market order so fractional inventory
can be represented; outside regular hours it may queue at Robinhood.

## GFD versus GTC

- GFD expires at the end of the selected provider trading day/session.
- GTC can remain working at Robinhood for up to 90 calendar days.

GRANDE Alpha detects a pending broker order and will not submit another one. Only the explicit
**STOP + CANCEL** flow can request cancellation: it previews the exact GRANDE-owned nonterminal
Agentic orders, requires confirmation, excludes manual/unrelated orders, and verifies an already
pending cancellation without sending it twice. Disconnect and orderly shutdown never cancel; they
lock and refuse while owned open or unresolved state remains. A GTC order can still survive an
application, network, operating-system, or power failure and may fill without the app running. Choose
GTC only if that persistence is intentional and monitor the authoritative Robinhood order view.

The local clock handles weekdays and selected session boundaries. It is not an exchange calendar;
holidays, halts, venue outages, liquidity, account restrictions, and final eligibility are enforced by
Robinhood review/placement and can still prevent or delay execution.

## Live shadow mode

Live shadow uses current Robinhood quotes and the same shared decision policy as replay/live logic,
but executes only fictional `TQQQS` and `SQQQS` positions. Its execution module does not import a
broker and contains no review, placement, or cancellation method. The active virtual ledger is
checkpointed locally so an unexpected supervisor restart cannot silently manufacture a fresh $50
run in the middle of the same market session.

### Procedure

1. Connect Robinhood so the app has read access to the Agentic account and current quotes.
2. Do **not** authorize a live session.
3. Select **Start Live Shadow**. A receipt states that broker calls are prohibited and real-order
   authority is absent.
4. Leave it running through completed QQQ bars. The Live shadow card shows virtual P/L and the
   current fictional position; every virtual fill appears in Receipts.
5. Select **Stop Live Shadow**, **STOP + CANCEL**, Disconnect, or exit. Shadow authority is revoked
   immediately and a final receipt records virtual equity, P/L, fills, and ending position. In the
   shadow-only runtime none of these controls can request broker cancellation.

Shadow mode and live order authority are mutually exclusive in both the controller and UI. Starting
shadow while a live grant exists is rejected; authorizing or starting real automation while shadow
is active is rejected. Stopping shadow never sells a real position because it never owned one.

Because shadow consumes the live controller's signal stream, GRANDE Alpha overwrites its strategy,
signal, exit, and trading-window fields with the selected runtime settings at start. Virtual sizing
and cost assumptions still come from the sandbox profile. The default runtime champion is
**CASH / hold**, which emits only a flat signal and requests no TQQQ or SQQQ position. Selecting a
different supported runtime policy is deliberate, changes the strategy fingerprint, and does not
imply profitability or unlock real orders. Changing runtime settings stops the active shadow run so
old virtual execution assumptions cannot continue under a new signal configuration.

### Durable restart boundary

Every shadow start, completed analysis advance, virtual fill, and stop appends an immutable SQLite
checkpoint. Each checkpoint is versioned, atomically committed, and hash-chained to the preceding
checkpoint in that run. The canonical hashed payload includes settled and T+1-unsettled cash, equity, P/L,
the open virtual position and fill history, pending transition, analysis and entry counters,
session loss controls, recent volatility inputs, prior prices, and the deterministic RNG state.

Recovery is deliberately narrow. GRANDE Alpha resumes only the latest checkpoint when all of these
identities match exactly:

- market session;
- hashed Agentic account identity;
- candidate strategy fingerprint and analysis cadence;
- candidate execution-contract fingerprint and checkpoint schema.

A corrupt or gapped chain, a changed candidate, a different account, a different market session,
or an active checkpoint with an incompatible contract blocks shadow startup. The app records a
critical `shadow_recovery` receipt instead of resetting cash, unsettled proceeds, P/L, entry count,
or an open position. A normally stopped prior run is not resumed and the next start creates a new
run explicitly.

After a process restart, the virtual execution ledger and its causal controls are restored, while
the controller's live QQQ bar/indicator pipeline deliberately warms up again from newly observed
quotes. The recovery receipt states that boundary; it must not be described as uninterrupted market
data continuity. Checkpointing remains local and read-only with respect to Robinhood: it grants no
authority and invokes no broker review, placement, or cancellation path.

### Example monitored engineering session

This example session is **shadow only**. Its purpose is to validate the application, data path, timing,
receipts, and virtual ledger under observation. It is not an attempt to earn money or a test of real
execution.

1. Open **Live Readiness**, run safe checks, and stop if account identity, positions, open orders,
   evidence lock, or read-only connectivity is unexpected.
2. Keep **Real-order automation** disabled. Do not select **Authorize Live Session**, do not enter a
   live phrase, and do not place a manual companion trade in the same symbols.
3. Connect read access, record the quote source and timestamps, and start Live Shadow before the
   intended regular-session observation window.
4. Observe without retuning. The controller polls quotes and locally constructs completed 5-second
   midpoint bars. These are not native 5-second historical bars. If comparing against remote
   history, label its finest available interval as 1 minute and do not treat the two paths as equal.
5. Use the default `cash_t1` ledger. A virtual sale moves proceeds to unsettled cash; those proceeds
   stay in equity but cannot buy again until the next observed market session.
6. Monitor data age, missing/coalesced polls, decisions, virtual fills, cash buckets, and the broker
   account independently. Press **STOP + CANCEL** on any mismatch; in shadow it revokes local
   authority and still makes no real sale.
7. Stop shadow deliberately, export the final receipt, and record ending position, settled cash,
   unsettled cash, P/L, warnings, and any gaps. Do not change policy parameters based on the result
   and then describe the same session as out-of-sample evidence.

Engineering success means no order capability was created, no real order was submitted, timestamps
and state transitions were explainable, `cash_t1` prevented unsettled-cash reuse, and the final
receipt reconciled. A positive virtual P/L is neither required nor sufficient.

### What shadow validates

Shadow is useful for current data flow, strategy state changes, timing, virtual accounting, and
operational monitoring. Its `cash_t1` behavior validates only the app's modeled ledger; it does not
validate real fills, queue position, broker acceptance, the broker's actual settled-funds
availability, taxes, or profit. A shadow result cannot automatically promote itself, and one day
cannot establish a profitable strategy.
