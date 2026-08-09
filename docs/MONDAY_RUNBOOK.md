# First live-session runbook

Real-order automation is optional and high risk. Complete the [research quickstart](QUICKSTART.md), evidence checks, and a monitored shadow session first. No result proves future profitability.

## Before enabling anything

- Read [Safety and compliance](SAFETY_AND_COMPLIANCE.md) and current provider/fund disclosures.
- Resolve account, tax, legal, immigration, employment, and suitability questions with qualified professionals.
- Keep the broker's official app available as an independent emergency control.
- Use only money whose complete loss would not impair rent, tuition, taxes, debt payments, emergency reserves, or other obligations.

## Enable capabilities

1. Confirm Evidence Lab shows `LIVE_REVIEW_ELIGIBLE` for current historical data and the exact current strategy. If it does not, remain in sandbox or shadow mode.
2. Open **Settings & Permissions**.
3. Enable **Broker connection** and save. Complete OAuth only on the provider's site.
4. Compare account identity, value, buying power, positions, and open orders in both applications.
5. Stop on any discrepancy, restriction, stale quote, pending order, or unexplained position.
6. Return to settings, enable **Real-order automation**, and type the exact settings phrase.

This enables controls, not a standing trading session. The app checks the certificate again before
authorization and before strategy start; missing, expired, or mismatched evidence fails closed.

## Grant one bounded session

1. Select **Authorize Live Session**.
2. Use a short duration and conservative limits for order notional, total exposure, session loss, trade count, rate, spread, and quote age.
3. Read the exact account, buying power, expiry, and limits.
4. Complete the attestation only if it is true.
5. Type the displayed session phrase and authorize.
6. Start once and monitor both the app and the broker.

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
