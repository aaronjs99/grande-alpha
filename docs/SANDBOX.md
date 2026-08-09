# GRANDE Alpha sandbox

The **SANDBOX** tab replays the momentum algorithm without connecting to Robinhood. `TQQQS` and
`SQQQS` are deliberately fictional symbols. They represent historical TQQQ and SQQQ prices inside
the virtual engine and can never be passed to the live broker.

## Run a replay

1. Open GRANDE Alpha and select **SANDBOX**. A Robinhood connection is not required.
2. Keep **Historical one-minute data** selected for a recent replay, or select the deterministic
   offline scenario when the historical service is unavailable.
3. Choose a preset, then edit any field. Presets only populate the controls; they are not claims
   that the settings are profitable.
4. Select **Run isolated sandbox**.
5. Review final virtual equity, P/L, return, maximum drawdown, completed round trips, win rate,
   raw buy-and-hold comparisons, the equity plot, and every virtual fill.

Each completed run and its configuration are saved to the local audit database. Running a replay
does not grant live authority and does not change the live strategy settings.

## Editable assumptions

- Calendar lookback from one to seven days.
- Starting virtual cash and per-entry notional.
- Per-side slippage and per-order commission.
- Warm-up, fast and slow EMA periods, trend threshold, and momentum horizon.
- Hard stop, take-profit, maximum holding time, and maximum entries per market day.
- Opening and closing no-trade windows.

The defaults mirror the live strategy where comparable. The historical dataset normally contains
five market sessions in a seven-calendar-day window because weekends and market holidays have no
regular-session candles.

## Accounting and timing model

- A signal is calculated only after a QQQ one-minute bar closes.
- The resulting virtual transition fills at the next aligned bar's open, plus configured slippage.
  This prevents same-bar look-ahead.
- Fractional virtual shares are supported.
- Only one of `TQQQS` or `SQQQS` can be held at a time.
- Exit orders are allowed even when the daily entry cap has been reached.
- Any remaining virtual position is flattened at the final available close.
- Buy-and-hold comparisons are unadjusted price returns and do not include slippage, commission,
  dividends, tax, financing, or execution constraints.

## Data limitations

Recent one-minute candles are downloaded from Yahoo Finance's chart service at run time. This is an
external, unsupported dependency and its availability or schema can change. The app aligns QQQ,
TQQQ, and SQQQ by timestamp and drops incomplete candles. It does not represent queue position,
bid/ask depth, partial fills, halts, rejected orders, taxes, settlement, or market impact.

The offline dataset is deterministic generated data and is labeled as a scenario, never as market
history. Neither source establishes future profitability. Seven calendar days is far too little
evidence for promotion to live settings; compare many non-overlapping periods and preserve a final
out-of-sample period before changing live limits.

## Isolation guarantee

The sandbox widget receives the audit store but no broker object. Its execution engine has no
review, placement, cancellation, OAuth, account-number, or Robinhood method. Sandbox runs use
separate `sandbox_runs` and `sandbox_fills` database tables and the aliases are rejected by the
live TQQQ/SQQQ path by construction.
