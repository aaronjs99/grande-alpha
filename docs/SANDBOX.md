# GRANDE Alpha sandbox

## Runtime quote-trace replay

`load_runtime_quote_trace()` can reconstruct a provenance-bound `HistoricalBundle` from GRANDE
Alpha's own synchronized QQQ/TQQQ/SQQQ quote ledger without calling a broker or opening the database
for writes. `RuntimeObservationReplayEngine` then feeds completed QQQ mid bars and their first later
causal target-ETF bid/ask batch through the same strategy and live-shadow execution path. It does not
apply the generic sandbox's additional next-bar scheduling or modeled spread to those exact quotes.

For regular-hours replay, premarket, after-hours, holidays, and overnight bridges are excluded.
Atomic batch IDs prove which quotes came from one accepted provider response; a stream ID proves the
runtime reset boundary. Legacy unbound rows are excluded and cannot affect frames or their hash.
A stream spanning sessions is rejected because it cannot represent the scheduled clean-start
contract. The default history retention is 240 calendar days (legacy default 90 migrates to 240),
and pruning deletes child quotes and now-empty batch parents together. The quote ledger has no trade volume, so volume is
zero/unknown and the trace cannot validate volume-dependent capacity. A short trace—even a perfect
one-day trace—is an engineering parity artifact, not sufficient evidence breadth and not evidence of
future profitability.

The **SANDBOX** tab is an isolated research environment. `TQQQS` and `SQQQS` are fictional aliases
for historical TQQQ and SQQQ prices. The widget receives an audit store but no broker object; its
engine has no OAuth, account, review, placement, or cancellation dependency.

## Data sources

| Source | Maximum lookback | Intended use |
|---|---:|---|
| Recent 1-minute cache | 7 calendar days | Short execution inspection at the finest current remote-history interval |
| 5-minute cache | 60 calendar days | Several-week robustness checks |
| Hourly cache | 730 calendar days | Broad regime and walk-forward checks |
| Full shared-history daily cache | Common history since February 2010 | Offline policy research and long-regime checks |
| Combined custom-second CSV import | 10,000 days in configuration | Source-observed 1-300 second evidence when provenance supports that interval |
| Combined 1-minute CSV import | 10,000 days in configuration | Licensed or personally exported long history |
| Deterministic scenario | 10,000 days | Offline repeatability and software testing only |

The built-in market download uses Yahoo's unsupported chart endpoint and writes a dated local cache
under `%LOCALAPPDATA%\GRANDEAlpha\sandbox_cache`. It is convenient research data, not a licensed
feed or an execution-quality record. True 6–24 month minute research requires a separate lawful
dataset. Extended-session research requests pre/post bars, but the adapter refuses to label that
source as complete 24-hour coverage. Overnight certification requires an aligned CSV that actually
contains the full selected session. Import one combined CSV with:

```text
timestamp,symbol,open,high,low,close,volume,market_hours
2026-08-03T13:30:00+00:00,QQQ,100,101,99,100.5,123456,regular_hours
```

Include aligned rows for `QQQ`, `TQQQ`, and `SQQQ`. Each run reports aligned bars, sessions,
complete-session coverage, missing intervals, duplicates, zero-volume bars, and a SHA-256 content hash. The hash lets two runs
prove that they used identical candles; it does not prove the vendor data is correct.
`market_hours` is optional for regular/extended imports. To claim `all_day_hours`, declare that exact
value consistently and include both evening and overnight observations; missing intervals are then
checked across the broker trading-date boundary.
The current remote-history adapter accepts `1m`, `5m`, or `60m`; it does not supply native 5-second
historical bars. The live controller instead constructs each completed 5-second bar locally from
the quote midpoints it actually observes. Do not split, repeat, interpolate, or forward-fill a
1-minute candle into twelve rows and call it 5-second evidence. Choose the custom-second import and
set 5 seconds only when the file contains genuinely observed or source-provided 5-second bars with
documented provenance. A 1-minute dataset and a locally derived 5-second stream have different
information, timing, hashes, and strategy fingerprints; neither certifies the other.

## Timing and execution

- Signals use completed QQQ bars only and transitions begin at the next aligned bar.
- Virtual buys pay modeled half-spread, slippage, and commission; sells receive the inverse.
- The execution profile matches live vocabulary: regular, extended, or 24 Hour Market; market or
  whole-share limit where provider-valid; GFD or GTC for limits; and a marketable-limit offset.
- Modeled limits remain unfilled when costs exceed the configured cap. GFD pending intents clear at
  the next session; GTC pending intents may carry into the next modeled session.
- Spread can widen with the candle's intrabar range.
- Configurable latency, fill fraction, rejection probability, and maximum volume participation
  produce an auditable event even when no fill occurs.
- Size is limited by order cap, equity exposure, stop-distance risk budget, available cash, and an
  optional volatility target.
- The default `cash_t1` model splits settled and unsettled cash. Buys draw only from settled cash;
  sale proceeds move to unsettled cash and remain there until the engine observes the next market
  session. Unsettled cash remains in displayed equity but cannot fund another modeled entry.
- Daily-loss and consecutive-loss pauses stop new entries. Exits remain possible.
- The shared policy targets cash during the configured close window and allows exits through the
  selected session's close. Missing bars, rejected virtual fills, or a truncated dataset can still
  leave a position. **Close virtual positions at every session end** uses each session's last
  available candle as a forced modeling fill; it prevents accidental overnight replay exposure
  but is not evidence that a real closing order would fill at that price.
  evidence eligibility always requires an actually flat replay result.

`cash_t1` is a conservative session-level approximation, not a broker ledger. It does not model
holidays, asset-specific exceptions, broker holds, good-faith restrictions, or the exact timestamp
at which funds become available. The broker's reported buying power is authoritative. Selecting
`instant` is a research counterfactual and can materially overstate how often a cash account can
trade.

Other approximations do not model queue priority, order-book depth, halts, taxes, short-lived quote
changes, or the market impact of a real order.

## Results and experiment workflow

1. Select one source and lookback; for CSV, choose the file.
2. State the hypothesis in **Run note** and choose or edit a configuration.
3. Run the sandbox and inspect return, drawdown, profit factor, expectancy, Sharpe, Sortino,
   exposure, turnover/costs, ending position, settled cash, unsettled cash, equity curve, and fill
   ledger.
4. Use the slider or Play control to replay the equity state. Selecting a fill exposes its exact
   timestamp, requested quantity, filled fraction, price, cost, reason, and resulting cash.
   The synchronized trade timeline shows both fictional ETF price paths with upward buy markers,
   downward profitable-sale markers, X-shaped losing-sale markers, and square flat-sale markers.
   Hover a marker for its time, modeled price, realized P/L, and exit reason; click it to select the
   matching fill-ledger row.
5. Compare presets on the same dataset. This tournament is exploratory and does not make the
   winning row out-of-sample. Then run the [evidence lab](EVIDENCE_LAB.md) on the exact candidate.
6. Load prior configurations from **Saved runs** or export the virtual fills as CSV.

For daily history, select **Community remote: full shared history (daily)** and use the
**9-action lab** tab. Its learner is deliberately isolated from broker controls and records every
holdout action. See [Action Lab](ACTION_LAB.md).

Every completed run stores its configuration, metrics, fills, execution events, note, data source,
and dataset hash in the local audit database. It never changes live settings or authority.

## Interpretation boundary

A profitable replay is a hypothesis result, not a promise, recommendation, or proof of future
profitability. Leveraged ETFs reset daily; path dependence and volatility decay make long-period
outcomes especially sensitive to sequence. Inspect costs, unfavorable periods, parameter
neighbors, and untouched test folds before increasing confidence.
