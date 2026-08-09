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

## What shadow validates

Shadow is useful for current data flow, strategy state changes, timing, virtual accounting, and
operational monitoring. It does not validate real fills, queue position, broker acceptance,
settled-funds availability, taxes, or profit. A shadow result cannot automatically promote itself.

