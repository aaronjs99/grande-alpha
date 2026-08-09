# GRANDE Alpha sandbox

The **SANDBOX** tab is an isolated research environment. `TQQQS` and `SQQQS` are fictional aliases
for historical TQQQ and SQQQ prices. The widget receives an audit store but no broker object; its
engine has no OAuth, account, review, placement, or cancellation dependency.

## Data sources

| Source | Maximum lookback | Intended use |
|---|---:|---|
| Recent 1-minute cache | 7 calendar days | Short execution inspection |
| 5-minute cache | 60 calendar days | Several-week robustness checks |
| Hourly cache | 730 calendar days | Broad regime and walk-forward checks |
| Combined CSV import | 3,650 days in configuration | Licensed or personally exported long history |
| Deterministic scenario | 3,650 days | Offline repeatability and software testing only |

The built-in market download uses Yahoo's unsupported chart endpoint and writes a dated local cache
under `%LOCALAPPDATA%\GRANDEAlpha\sandbox_cache`. It is convenient research data, not a licensed
feed or an execution-quality record. True 6–24 month minute research requires a separate lawful
dataset. Import one combined CSV with:

```text
timestamp,symbol,open,high,low,close,volume
2026-08-03T13:30:00+00:00,QQQ,100,101,99,100.5,123456
```

Include aligned rows for `QQQ`, `TQQQ`, and `SQQQ`. Each run reports aligned bars, sessions,
missing intervals, duplicates, zero-volume bars, and a SHA-256 content hash. The hash lets two runs
prove that they used identical candles; it does not prove the vendor data is correct.

## Timing and execution

- Signals use completed QQQ bars only and transitions begin at the next aligned bar.
- Virtual buys pay modeled half-spread, slippage, and commission; sells receive the inverse.
- Spread can widen with the candle's intrabar range.
- Configurable latency, fill fraction, rejection probability, and maximum volume participation
  produce an auditable event even when no fill occurs.
- Size is limited by order cap, equity exposure, stop-distance risk budget, available cash, and an
  optional volatility target.
- Daily-loss and consecutive-loss pauses stop new entries. Exits remain possible.
- The ending virtual position remains open and is marked to the last close by default. Select
  **force flat at end** only when that assumption matches the experiment.

These approximations do not model queue priority, order-book depth, halts, taxes, settlement,
short-lived quote changes, or the market impact of a real order.

## Results and experiment workflow

1. Select one source and lookback; for CSV, choose the file.
2. State the hypothesis in **Run note** and choose or edit a configuration.
3. Run the sandbox and inspect return, drawdown, profit factor, expectancy, Sharpe, Sortino,
   exposure, turnover/costs, ending position, equity curve, and fill ledger.
4. Use the slider or Play control to replay the equity state. Selecting a fill exposes its exact
   timestamp, requested quantity, filled fraction, price, cost, reason, and resulting cash.
5. Compare presets on the same dataset. Then run the [evidence lab](EVIDENCE_LAB.md).
6. Load prior configurations from **Saved runs** or export the virtual fills as CSV.

Every completed run stores its configuration, metrics, fills, execution events, note, data source,
and dataset hash in the local audit database. It never changes live settings or authority.

## Interpretation boundary

A profitable replay is a hypothesis result, not a promise, recommendation, or proof of future
profitability. Leveraged ETFs reset daily; path dependence and volatility decay make long-period
outcomes especially sensitive to sequence. Inspect costs, unfavorable periods, parameter
neighbors, and untouched test folds before increasing confidence.
