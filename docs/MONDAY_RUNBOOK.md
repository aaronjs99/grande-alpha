# First-session runbook

Real-order automation is optional and high risk. Complete the [research quickstart](QUICKSTART.md), evidence checks, and a monitored shadow session first. No result proves future profitability.

## Tomorrow: Tuesday, August 11, 2026

Tomorrow is a **shadow-only engineering session**. No strategy currently has a live certificate, so
do not enable Real-order automation, do not select **Authorize Live Session**, and do not submit an
order. The objective is to prove that the read path, locally derived bars, policy timing, receipts,
and virtual settlement ledger behave coherently under monitoring.

1. Run **Morning Check.cmd** before the market opens. Save its output and stop on any unexpected
   account identity, position, open order, read failure, or evidence state.
2. Keep the broker's official app visible as an independent read-only cross-check. Do not manually
   trade TQQQ or SQQQ during the observation.
3. Start **Live Shadow** before the chosen regular-session window and record the start receipt,
   strategy fingerprint, quote timestamps, and initial settled cash.
4. Observe without retuning. The live path constructs completed 5-second bars from polled quote
   midpoints. The current remote-history path supplies 1-minute bars at its finest interval; it is
   not native 5-second history and cannot validate the 5-second stream.
5. Confirm `cash_t1` behavior: virtual buys use settled cash, virtual sale proceeds become
   unsettled, those proceeds remain in equity, and they cannot fund another entry until the next
   observed market session.
6. Stop immediately on stale data, an unexplained transition, a broker/app mismatch, or any real
   order. End shadow deliberately and save the final receipt.
7. Record bar gaps, decisions, virtual fills, settled and unsettled cash, ending position, warnings,
   and virtual P/L. Do not interpret a positive day as evidence of a durable edge.

A valid engineering result has zero real-order authorization, zero submitted real orders,
explainable timing and state transitions, correct `cash_t1` cash buckets, and a reconcilable final
receipt. Profit is not an acceptance criterion.

For future weekdays, the optional [scheduled live-shadow setup](SCHEDULED_SHADOW.md) can launch the
application at 6:20 AM local time with `--auto-shadow`. Installation is explicit and per-user; a
scheduled launch still must pass the application's broker and readiness checks before virtual fills
begin. Keep the application monitored.

## Before enabling anything

- Run **Morning Check.cmd**. Continue only if the broker read path passes and the app reports the
  expected account, no unexplained position, and no open order.
- Read [Safety and compliance](SAFETY_AND_COMPLIANCE.md) and current provider/fund disclosures.
- Resolve account, tax, legal, immigration, employment, and suitability questions with qualified professionals.
- Keep the broker's official app available as an independent emergency control.
- Use only money whose complete loss would not impair rent, tuition, taxes, debt payments, emergency reserves, or other obligations.
- If the Agentic account is a cash account, treat broker buying power as authoritative. Same-day sale
  proceeds may be unavailable until settlement, so the same dollars cannot support a continuous
  sequence of intraday entries. GRANDE Alpha's `cash_t1` model moves virtual sale proceeds to
  unsettled cash and releases them only when the next market session is observed; this is a
  conservative session-level approximation, not the broker ledger.

## Later live review, only after evidence passes

The remaining sections are not tomorrow's procedure. Use them only if a future policy-v9 Evidence
Lab run produces a current `LIVE_REVIEW_ELIGIBLE` certificate for the exact strategy, cadence,
execution route, settlement model, and risk envelope, and all personal compliance questions have
been resolved.

## Enable capabilities

1. Confirm Evidence Lab shows `LIVE_REVIEW_ELIGIBLE` for current historical data and the exact current strategy. If it does not, remain in sandbox or shadow mode.
2. Open **Settings & Permissions**.
3. Enable **Broker connection** and save. Complete OAuth only on the provider's site.
4. Compare account identity, value, buying power, positions, and open orders in both applications.
5. Stop on any discrepancy, restriction, stale quote, pending order, or unexplained position.
6. Return to settings, enable **Real-order automation**, and type the exact settings phrase.

This enables controls, not a standing trading session. The app checks the certificate again before
authorization, strategy start, every automatic decision, broker review, and final placement call.
Changing any runtime setting revokes the grant and stops the strategy; missing, expired, malformed,
or mismatched evidence fails closed.

## Grant one bounded session

1. Select **Authorize Live Session**.
2. Use a short duration and conservative limits for order notional, total exposure, session loss, trade count, rate, spread, and quote age.
3. Review the selected regular, extended, or 24 Hour Market route. Extended and overnight routes
   require whole-share limits; use GTC only if persistence after an app failure is intentional.
4. Read the exact account, buying power, expiry, and limits.
5. Complete the attestation only if it is true.
6. Type the displayed session phrase and authorize.
7. Start once and monitor both the app and the broker.

Increasing a cap does not increase expected edge; it increases possible exposure.

## Stop conditions

Press **STOP + CANCEL** if the applications disagree, data goes stale, an unexpected order appears, a limit is reached, behavior differs from documentation, or you cannot continue monitoring. Then confirm order state in the broker. Cancellation does not liquidate a filled position.

Use **Flatten Position** only after reviewing the exact quantity and broker preview. Slippage and rejection remain possible.

## End of session

1. Press **STOP + CANCEL**.
2. Confirm no order is open or pending in the broker.
3. Deliberately handle any remaining position.
4. Save broker fills and fees, and complete the [journal](DAILY_JOURNAL_TEMPLATE.md).
5. Disable real-order permission in settings if it is not needed again soon; forget credentials if appropriate.
