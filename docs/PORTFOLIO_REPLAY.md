# Multi-day portfolio paper replay

Status: offline implementation with synthetic regression tests. No broker connection, strategy
certificate, historical performance claim or live-trading authority is produced.

```powershell
.\cli.ps1 portfolio replay --input C:\private\portfolio-replay.json
.\cli.ps1 portfolio replay-report --input C:\private\portfolio-replay.json
```

Supply exactly these top-level fields: `initial_cash`, `daily_loss_usd`, `slippage_bps`, `fee_bps`,
`max_quote_age_seconds`, `max_execution_delay_seconds`, and `frames`. All numeric inputs must be
explicit and finite; costs may be zero. A frame has exactly:

- `request`: a [mixed allocation request](MIXED_PORTFOLIO.md). Its holdings must be empty because
  the replay builds its own book from initial cash; its capital is replaced with current virtual NAV.
- `quotes`: a symbol map, each with `bid`, `ask`, `observed_at`, `available_at`. Every earnings,
  held and pending asset needs a quote. Earnings-screen quotes must match the tape.
- `theses`: explicit symbol-to-boolean current thesis status for held and pending assets.
- `corporate_actions`: an empty list. Nonempty events reject the replay until adjustments exist.

Frames must be strictly chronological and within scheduled regular sessions. Allocation policy and
earnings thresholds are frozen across the run. Inputs and costs are bound into a reproducible hash.
Quote and risk provenance remains user-supplied and unverified.

## Execution and accounting contract

The first frame only plans. A target can fill on a subsequent frame only if that frame's quotes are
strictly later than the decision and within the explicit execution-delay limit. Expired or noncausal
targets are reported as skipped, not silently filled. Fresh planning follows each simulated execution.

Virtual buys use ask plus slippage; sells use bid minus slippage. Proportional fees apply to each
fill. Share amounts are rounded down to six decimal places, except complete exits of the tracked
quantity. Buys are limited by settled cash. Sale proceeds remain unsettled until the next scheduled
equity trading day, so same-day rotations cannot reuse those proceeds. This scheduled calendar model
is not provider-observed settlement and does not include emergency closures.

Positions retain their original opening timestamps. Current invalid theses block purchases; holding
reviews are evaluated by the allocator. NAV includes settled cash, unsettled cash, and midpoint-marked
holdings. Daily-loss checks include the prior observed NAV when entering a new day, so an overnight
gap cannot simply reset away a loss. A latched daily stop prevents further buys that day, not sells;
it is not a guarantee of maximum realized loss. Threshold checks also occur after simulated fills.

The final frame's targets remain unexecuted. Residual positions are reported rather than forcibly
sold at an invented closing price. The only included benchmark is unchanged starting cash.

## What this does not establish

This is a deterministic full-fill model without queue priority, market impact, volume limits,
partial fills, dividends, splits, taxes, or delisting recovery. Prices can move between supplied
observations. Sector/factor estimates are not validated here. It is not yet a qualification-grade
historical backtest or proof of live runtime parity. Use real point-in-time data, additional stress
models, out-of-sample benchmarks and the provider execution lifecycle before considering promotion.

For true forward observation, append each near-real-time frame as it arrives; the recorder rejects
late backfills and nonchronological inserts. Reporting reruns the exact replay over the immutable
stored frames.

```powershell
.\cli.ps1 portfolio forward-append --database C:\private\forward.db --input C:\private\frame.json
.\cli.ps1 portfolio forward-report --database C:\private\forward.db --settings C:\private\replay-settings.json
```

Individual-stock execution and multi-day recovery now exist behind the separate
[mixed production qualification](PRODUCTION_QUALIFICATION.md) gate. This replay alone does not
broaden the current public live session contract.
