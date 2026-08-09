# Troubleshooting

## Sandbox historical download fails

Select **Offline deterministic scenario** and run again. Historical one-minute data comes from an
external chart service that can be unavailable or change without notice. Offline results are
generated scenarios, not a substitute for historical validation. Neither source requires or uses
your Robinhood connection.

## Connect Robinhood opens a browser but never finishes

1. Complete login only on a Robinhood domain.
2. Allow the browser to return to `http://localhost:37654/callback`.
3. Ensure another GRANDE Alpha process is not running.
4. Disconnect/reconnect Robinhood Agentic Trading if Robinhood reports an expired authorization.
5. Check `%LOCALAPPDATA%\GRANDEAlpha\grande_alpha.log`.

The OAuth wait expires after five minutes. Tokens and registered-client information are stored in
Windows Credential Manager under `GRANDEAlpha.RobinhoodMCP`. A legacy
`MomentumTrader.RobinhoodMCP` credential is copied into the new namespace when first used.

## Broker and app balances disagree

Do not authorize live trading.

1. Press **Disconnect**.
2. Confirm the Agentic account—not the default margin account—is selected in Robinhood.
3. Confirm deposits are complete and buying power is actually available.
4. Reconnect OAuth and refresh.
5. If the mismatch remains, use Robinhood support before trading.

The app deliberately refuses to authorize when the provider reports zero account value or zero
buying power. Never authorize around an unexplained discrepancy of any size.

## Quotes are stale or missing

- Confirm the U.S. equity session is open.
- Check internet connectivity and Robinhood status.
- Press STOP + CANCEL.
- Reconnect rather than weakening the quote-age limit.

The app blocks orders when the relevant quote is older than eight seconds or the spread exceeds the
session cap.

## Strategy remains FLAT

This can be correct. Check the receipt text:

- `Warm-up N/24` means insufficient completed QQQ bars.
- Small EMA separation or momentum disagreement intentionally produces FLAT.
- Outside the allowed time window, the risk engine blocks orders even if the signal is directional.

Do not increase size or manually force an order merely because no automatic trade appeared.

## Robinhood review warning locked the strategy

The warning is intentionally fail-closed and may describe buying power, settlement, tradability,
market-hours, halt, or a new broker control.

1. Read the complete warning in Receipts and Robinhood.
2. Press STOP + CANCEL.
3. Resolve the broker condition.
4. Reauthorize a new live session only after the reason is understood.

Do not change the code to ignore unknown warnings.

## STOP + CANCEL was pressed but a position remains

Canceling an order does not reverse an already completed fill.

1. Inspect the order state in Robinhood.
2. If a sell is appropriate, select **Flatten Position**.
3. Review the exact quantity and market disclosure.
4. Type the displayed sell phrase.
5. Verify the resulting state and final position in Robinhood.

## App was closed or crashed with a position

The local stop and target are unavailable while the app is down. Open Robinhood immediately, inspect
the position and orders, and decide whether to exit manually. Do not assume the desktop app placed a
protective broker order.

## Second copy will not open

Only one trading instance is permitted. Bring the existing window forward. If the prior process
crashed, wait ten seconds and relaunch so the stale instance lock can clear.

## Verification commands

From PowerShell in the project directory:

```powershell
.\verify.ps1
.\build.ps1
```

The verified executable is `dist\GRANDEAlpha\GRANDEAlpha.exe`.
