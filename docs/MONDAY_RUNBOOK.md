# Monday live-trading runbook

This is the operating procedure for the Robinhood Agentic account. The app transmits real-money
orders only after browser OAuth and a time-limited live grant. It starts **LOCKED** on every launch.

## Before Monday

- Read [strategy and profit mechanics](STRATEGY_AND_PROFIT.md).
- Obtain the F-1/tax guidance described in [safety and compliance](SAFETY_AND_COMPLIANCE.md).
- In Robinhood, confirm the Agentic account is active and connected. The supplied screenshot shows
  $50, but an earlier connected-account response returned $0. Treat that disagreement as unresolved
  until the desktop app and Robinhood show the same current value.
- Keep the Robinhood mobile app available as an independent emergency control.
- Do not place manual TQQQ/SQQQ orders while the strategy is running.

## Recommended first-session limits for a $50 account

These are deliberately smaller than the app's editable defaults:

| Setting | Monday value | Consequence |
|---|---:|---|
| Session duration | 60 minutes | Authority expires automatically |
| Maximum order | $15 | At most 30% of the account per entry |
| Maximum total exposure | $25 | At least half the account remains unexposed |
| Maximum session loss | $1 | Stops new entries near a 2% account drawdown |
| Maximum submitted orders | 4 | At most two entry/exit round trips |
| Maximum orders/minute | 1 | Prevents rapid flip-flopping |
| Maximum spread | 20 bps | Wide markets are rejected |

The software defaults are $25/order, $40 exposure, $2 loss, six orders, and two orders/minute. For a
$50 account those defaults are aggressive. Increasing them does not improve the strategy's edge; it
only magnifies the outcome.

## 6:15–6:25 AM Pacific / 9:15–9:25 AM Eastern

1. Open Robinhood and the Agentic account.
2. Verify:
   - account value and buying power;
   - no unexpected TQQQ or SQQQ position;
   - no open or pending equity order;
   - no deposit, restriction, or settlement warning.
3. Double-click `Start GRANDE Alpha.cmd` or open the packaged `GRANDEAlpha.exe`.
4. Select **Connect Robinhood**.
5. Complete Robinhood OAuth in the browser. Enter credentials only on Robinhood's site.
6. Return to the app and compare its Agentic-account last four digits, value, buying power,
   positions, and orders to Robinhood.

### Stop here if

- the app shows $0 while Robinhood shows $50, or vice versa;
- the wrong account is displayed;
- either surface has an unexplained position or order;
- authentication, quotes, or portfolio refreshes show an error;
- Robinhood reports an account restriction.

Disconnect and troubleshoot. Do not authorize around a discrepancy.

## 6:30–6:55 AM Pacific / 9:30–9:55 AM Eastern

1. Leave the app connected and **LOCKED** while it records QQQ quotes.
2. The baseline requires 24 completed one-minute QQQ bars. Its regime card will show the warm-up
   count.
3. The risk engine independently prohibits entries during the first five minutes after the open.
4. Watch quote age and spread. QQQ, TQQQ, and SQQQ should update normally.
5. Do not force a trade because the regime remains **FLAT**. Flat is a valid decision.

## Authorize the live session

For the first session, prefer [live shadow mode](SHADOW_MODE.md) and complete a full observed market
session with no real-order authority. Stop shadow before following the live steps below. Shadow and
live authority cannot operate together.

After the account matches Robinhood and the warm-up is nearly complete:

1. Select **Authorize Live Session**.
2. Enter the Monday limits from the table above.
3. Read the exact account, value, buying power, expiry, and limits.
4. Check the own-account/F-1/tax attestation only if it is true.
5. Type the displayed `LIVE ####` phrase.
6. Select **Authorize live session**.
7. Confirm the authority card reads **LIVE** with an expiry time.
8. Select **Start Strategy** once.

The session grant permits automatic reviewed TQQQ/SQQQ orders until expiry. It is never remembered
across restarts. Robinhood warnings stop and lock the strategy instead of being ignored.

## While it is running

Monitor both the desktop app and Robinhood:

- **BULLISH** means the current target is TQQQ.
- **BEARISH** means the current target is SQQQ.
- **FLAT** means cash; an existing leveraged position is exited.
- An opposite regime exits the current ETF before considering the other ETF.
- An open order blocks another automatic submission.
- The receipt tab shows every signal, market-data disclosure, review, order, error, and authority
  change.

Do not react to a single flicker. The strategy changes only on completed one-minute bars.

## Emergency procedures

### STOP + CANCEL

Press **STOP + CANCEL** if:

- app and Robinhood disagree;
- an unexpected order or position appears;
- quotes stop updating;
- the strategy behaves differently from the documented rules;
- the loss limit or your personal discomfort threshold is reached;
- you need to leave the computer.

This immediately locks new app orders and attempts to cancel open agentic equity orders. Check the
Robinhood order state afterward. A cancel request can race a fill and is not guaranteed during a
network or Robinhood outage.

### Flatten Position

This is a separate action because canceling orders does not sell a filled position:

1. Select **Flatten Position**.
2. Read Robinhood's live market disclosure and the exact sell quantity.
3. Type the exact `SELL quantity SYMBOL` phrase.
4. Confirm the resulting order state in both the app and Robinhood.

The app uses a reviewed regular-hours market order for fractional positions. Slippage remains
possible.

## End of session

1. Press **STOP + CANCEL** even if the grant is about to expire.
2. Confirm no order is open or pending in Robinhood.
3. Decide deliberately whether any remaining position should be flattened. The strategy is designed
   for intraday use, not unattended overnight leveraged exposure.
4. Record the session in [the daily journal](DAILY_JOURNAL_TEMPLATE.md).
5. Save/export Robinhood fills and fees when available.
6. Exit the app. Its exit flow again attempts to lock and cancel, but filled positions remain yours.
