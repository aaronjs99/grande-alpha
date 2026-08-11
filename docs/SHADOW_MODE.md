# Live shadow mode

Live shadow uses current Robinhood quotes and the same shared decision policy as replay/live logic,
but executes only fictional `TQQQS` and `SQQQS` positions in memory. Its module does not import a
broker and contains no review, placement, or cancellation method.

## Procedure

1. Connect Robinhood so the app has read access to the Agentic account and current quotes.
2. Do **not** authorize a live session.
3. Select **Start Live Shadow**. A receipt states that broker calls are prohibited and real-order
   authority is absent.
4. Leave it running through completed QQQ bars. The Live shadow card shows virtual P/L and the
   current fictional position; every virtual fill appears in Receipts.
5. Select **Stop Live Shadow**, **STOP + CANCEL**, Disconnect, or exit. Shadow authority is revoked
   immediately and a final receipt records virtual equity, P/L, fills, and ending position.

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

## Tuesday, August 11, 2026 engineering session

Tomorrow's session is **shadow only**. Its purpose is to validate the application, data path, timing,
receipts, and virtual ledger under observation. It is not an attempt to earn money or a test of real
execution.

1. Run **Morning Check.cmd** before the market opens and save its output. Stop if account identity,
   positions, open orders, evidence lock, or read-only connectivity is unexpected.
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

## What shadow validates

Shadow is useful for current data flow, strategy state changes, timing, virtual accounting, and
operational monitoring. Its `cash_t1` behavior validates only the app's modeled ledger; it does not
validate real fills, queue position, broker acceptance, the broker's actual settled-funds
availability, taxes, or profit. A shadow result cannot automatically promote itself, and one day
cannot establish a profitable strategy.
