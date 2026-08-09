# GRANDE Alpha

GRANDE Alpha is a Windows desktop app for live, session-scoped TQQQ/SQQQ automation through
Robinhood Agentic Trading. It connects only through Robinhood's official Trading MCP endpoint,
uses browser OAuth, reviews every generated order with Robinhood, and records a local receipt for
every decision and broker response.

This is an intraday automation workstation, not exchange-colocated high-frequency trading. It does
not promise profit. TQQQ and SQQQ target leveraged **daily** returns and can lose value rapidly.

Start with the complete [operating documentation](docs/README.md), especially the
[Monday live runbook](docs/MONDAY_RUNBOOK.md) and
[strategy/profit mechanics](docs/STRATEGY_AND_PROFIT.md). To test without Robinhood or real
orders, use the isolated [TQQQS/SQQQS sandbox](docs/SANDBOX.md).

## Sandbox replay

Open the **SANDBOX** tab without connecting Robinhood. It downloads up to seven calendar days of
aligned QQQ/TQQQ/SQQQ one-minute candles—or uses a labeled deterministic offline scenario—and
replays virtual `TQQQS`/`SQQQS` orders. Strategy, execution, exit, capital, and frequency settings
are editable directly in the tab. Sandbox configuration and results never alter live authority or
live strategy settings.

## Monday startup

1. Open Robinhood and verify the Agentic account's buying power and that ChatGPT/Agentic Trading is
   connected. The app refuses to trade when the broker reports zero buying power.
2. Resolve F-1 and tax-status questions with your DSO/tax adviser. The app asks you to attest to
   this each live session; it does not decide your legal status.
3. Double-click `Start GRANDE Alpha.cmd`.
4. Select **Connect Robinhood** and complete browser OAuth. Credentials never enter this app.
5. Confirm the account nickname and last four digits, broker-reported value, buying power, quotes,
   and positions.
6. Select **Authorize Live Session**, set numeric caps, and type the displayed confirmation phrase.
7. Select **Start Strategy**. Keep Robinhood open independently for monitoring.

On a new morning, launch before 9:30 AM Eastern when practical. The first run needs 24 completed
one-minute QQQ bars before the baseline strategy has enough data; the warm-up status is shown in
the QQQ regime card and receipt log.

The red **STOP + CANCEL** button immediately blocks new orders and attempts to cancel every open
agentic equity order. It cannot guarantee cancellation if the network or Robinhood is unavailable.
It does not liquidate filled positions; use **Flatten Position** and confirm the exact sell preview.

## Strategy

The included deterministic strategy observes QQQ one-minute midpoint bars and chooses one state:

- bullish: buy/hold TQQQ;
- bearish: buy/hold SQQQ;
- uncertain: hold cash.

It uses EMA trend separation and short-horizon momentum. It never intentionally holds TQQQ and
SQQQ at the same time. Existing opposite positions are sold before a new direction is considered.
The strategy waits for warm-up data, does not trade the first five or last ten minutes of the
regular session, and applies a spread/staleness filter before every order.

These rules are an engineering baseline, not a proven market edge. Change thresholds in the live
session dialog only after understanding their effect.

## Security and authority

- Robinhood tokens and dynamically registered OAuth client information are stored in Windows
  Credential Manager through `keyring`, not plaintext files.
- Live authority expires automatically and is never remembered across app restarts.
- Numeric limits are enforced by a separate risk engine outside the strategy.
- Each logical order has a UUID idempotency key reused on transport retries.
- SQLite receipts and the Research Fund ledger live under
  `%LOCALAPPDATA%\GRANDEAlpha\grande_alpha.db`.

## Development

```powershell
.\setup.ps1
.\run.ps1
.\verify.ps1
.\build.ps1
```

The packaged app is written to `dist\GRANDEAlpha\GRANDEAlpha.exe`.

## External limitations

- Robinhood controls MCP availability, authentication, quotes, reviews, order acceptance, fills,
  settlement, and account restrictions.
- A cash Agentic account remains subject to settled-funds rules even after PDT changes.
- Robinhood stock orders do not provide native bracket/OCO orders. The app therefore does not
  claim that a local stop can protect a position while the app or network is down.
- Keep the Robinhood mobile app available as an independent emergency control.
