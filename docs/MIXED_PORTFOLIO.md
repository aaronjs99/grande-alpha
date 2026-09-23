# Mixed earnings and leveraged-ETF portfolio

Status: the allocation planner is research-only; a separate foreground mixed runner can use its
policy only after exact qualification, authorization, and broker checks. That runner is not
provider-observed or deployment-qualified by the presence of code alone. It does not widen the
older desktop TQQQ/SQQQ grant.

## Implemented research logic

- Earnings candidates pass the existing point-in-time EPS/price/quote screen. Invalid rows stop
  planning; repeated events for one symbol require deliberate event selection.
- Stock scores combine bounded surprise and post-announcement momentum strength, scaled down for
  volatility. They are heuristics, not return forecasts, confidence probabilities or proven alpha.
- The ETF sleeve selects TQQQ in a qualified bullish regime, SQQQ in a qualified bearish regime,
  and neither in neutral, stale, high-spread, excessive-volatility or low-confidence conditions.
  Regime/confidence inputs must be supplied; the planner does not claim to have estimated them.
- Relative stock/ETF scores change the proposed split. Cash retains unused capacity rather than
  automatically filling every cap. There is no requirement to invest the full account.
- Individual-stock, stock-sleeve, ETF-sleeve, sector and gross-exposure caps apply. The gross proxy
  counts stock dollars once and leveraged-ETF dollars three times, without inverse-hedge credit.
  This is not a covariance model, Nasdaq beta estimate, VaR or maximum-loss guarantee. ETF sector
  holdings are not looked through; the sector cap covers the stock sleeve only.
- Holdings carry their original open time across dates. Thesis failure or a configurable holding
  horizon produces an exit review even when the position is losing. The engine does not wait for
  profitability or imply that a review was executed. Position age and session authority are separate.

The template contains configurable **research starting points**, not optimized or activated settings:
80% maximum invested, 60% stock sleeve, 20% ETF sleeve, 20% per stock, 40% per stock sector, and a
1.0 gross-leverage proxy cap. Holding-review horizons start at 20 calendar days for stocks and two
for leveraged ETFs. The user may change these in research; live adoption requires qualification.
No personal account or capital amount is embedded in public defaults.

ProShares describes TQQQ as a daily-target fund. Returns over longer periods can differ from its
daily multiple; multi-day eligibility is not permission to ignore compounding and volatility risk.
See [the issuer's product explanation](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq).

## Offline use

```powershell
.\cli.ps1 portfolio template
.\cli.ps1 portfolio plan --input C:\private\mixed-research.json
```

The request includes `capital_usd` (total modeled portfolio value, including cash), all policy fields,
an earnings screen request, timestamped market-regime data, per-stock sector/
volatility metadata, and current long-only holdings. `daily_volatility` is a fractional return
standard deviation, not a percent number. Each holding has exactly `symbol`, `market_value`,
`opened_at`, and boolean `thesis_valid`. Holdings cannot exceed the supplied modeled account value.

Targets and rebalance reviews are not orders. Smaller changes are marked below a configurable review
band; the target allocation itself is not a promise that those trades are executable. Sales and
unsettled proceeds cannot be assumed to finance buys. Input and policy hashes support reproducibility;
input provenance remains user-supplied and unverified.

## Still required for a qualified live deployment

1. Establish lawful point-in-time earnings/consensus and licensed quote/risk inputs, including
   corporate actions and delistings; a configured API key is not a qualified dataset.
2. Run the multi-day replay on those inputs with costs, settlement, gaps, partial fills, volume,
   corporate actions, out-of-sample comparison, and parameter sensitivity.
3. Qualify the [mixed execution path](SYSTEM_ARCHITECTURE.md) against authenticated provider
   behavior, including eligibility, exits, reconciliation, and ambiguous submissions.
4. Validate multi-day operation, stop controls, and restart recovery in the intended deployment.
   Authority still expires; a recovered grant must be the same sole active, unexpired scope.
5. Meet every independent requirement in [production qualification](PRODUCTION_QUALIFICATION.md).

## Device-only notifications

New warning/error/critical receipts create a persistent notification in the same local audit
database. The live CLI also displays new notices in its existing terminal, with control characters
escaped. No email, webhook, external delivery service or pop-up process is used.

```powershell
.\cli.ps1 notifications --unread
.\cli.ps1 notifications --ack 12
.\cli.ps1 notifications --after 12
```

Acknowledgement marks one notification read; it does not erase its receipt, resume trading or
approve orders. The inbox survives restart. These are terminal/inbox notifications, **not Windows
toast notifications**. If the PC is off, frozen or disconnected, it cannot notify someone elsewhere.
GUI inbox integration and native toast delivery have not been implemented or tested.

## Earnings research: PEAD candidate screen

This command is a **research-only screen**. The replay, allocation planner, and gated mixed
runner described elsewhere in this guide are separate stages. A screen candidate by itself is
neither an evidence certificate nor authority to place an order; it does not widen the older
desktop TQQQ/SQQQ route.

Post-earnings-announcement drift is a research hypothesis worth evaluating, not an established
best trading marker. Historical research finds that costs can materially reduce apparent PEAD
profits; see [Ng, Rusticus and Verdi (2008)](https://experts.umn.edu/en/publications/implications-of-transaction-costs-for-the-post-earnings-announcem/)
and [Chordia et al. (2009)](https://business.columbia.edu/faculty/research/liquidity-and-post-earnings-announcement-drift).
Those studies are not evidence that this implementation will earn money now.

### Commands and input contract

```text
grande-alpha-cli earnings template
grande-alpha-cli earnings screen --input events.json
```

The template intentionally has no trading thresholds or usable market data. Populate the JSON from
an appropriately licensed, point-in-time dataset. The root fields are `schema_version` (1), `as_of`
(timezone-aware decision timestamp), `thresholds`, and `events`. All fields are mandatory; unknown
fields, duplicate JSON keys, nonfinite JSON constants, oversized files, and duplicate event IDs fail
closed. At most 10,000 events and 5,000,000 input bytes are accepted per request.

The caller must choose five strictly positive screening thresholds:

| Threshold | Meaning |
|---|---|
| `min_surprise_bps` | Minimum EPS surprise scaled by the pre-announcement price |
| `min_momentum_bps` | Minimum price movement after the post-announcement baseline |
| `max_spread_bps` | Maximum observed bid/ask spread divided by midpoint |
| `max_event_age_days` | Maximum elapsed calendar days since announcement |
| `max_quote_age_seconds` | Maximum age of the latest venue quote at decision time |

Each event binds:

- `event_id`, `symbol`, and `source_id` for traceability;
- `announced_at`, `actual_available_at`, and `consensus_available_at`;
- `actual_eps`, `consensus_eps`, their separate `actual_basis` / `consensus_basis`,
  `actual_period` / `consensus_period`, and `actual_currency` / `consensus_currency`;
- `reference_price`, `reference_at`, `reference_available_at`: an available pre-announcement price;
- `post_price`, `post_at`, `post_available_at`: an available post-announcement baseline;
- `bid`, `ask`, `quote_at`, `quote_available_at`, and `price_currency`: the later quote.

Timestamps need explicit offsets. Actual and consensus EPS must use matching accounting bases and
fiscal periods. Version 1 supports USD EPS and prices only. Consensus must have been available
strictly before the announcement. The post-announcement baseline cannot precede actual-result
availability; the latest quote must follow that baseline. No selected observation may become
available after `as_of`. Supply consistent split/corporate-action price and EPS bases; this screen
does not fetch or verify corporate-action records, exchange calendars, halts, or data rights.

### Exact calculations

- Surprise: `(actual_eps - consensus_eps) / reference_price * 10000`.
- Post-announcement momentum: `(((bid + ask) / 2) / post_price - 1) * 10000`.
- Spread: `(ask - bid) / ((bid + ask) / 2) * 10000`.

Surprise is **price-scaled EPS surprise**, not percentage EPS growth or standardized unexpected
earnings (SUE). A zero/negative consensus does not create a division-by-zero or reverse the sign.
Momentum measures movement after the supplied post-announcement baseline, not the initial gap.
This version screens positive surprise and positive subsequent momentum; it does not short misses.

### Output and remaining qualification

Each event is `RESEARCH_CANDIDATE`, `NO_CANDIDATE`, or `INVALID_INPUT`. Failed thresholds and invalid
chronology remain visible. Invalid rows cause exit status 2, even when other rows are valid.
The report includes the canonical input SHA-256 and explicitly reports unverified user-supplied
provenance, no live eligibility, no authority, and no backtest. A source ID is not license evidence.

Required before evaluating deployment: point-in-time expectations and release times; historical
universe including delistings; corporate-action consistency; chronological training/holdout splits;
portfolio capital and settlement accounting; entry/exit rules; commissions, spreads, slippage and
market impact; missing-data and restatement handling; benchmark comparisons; and provider-qualified
individual-stock execution. Never optimize a threshold on the same observations used to claim a result.

## Multi-day portfolio paper replay

Status: offline implementation with synthetic regression tests. No broker connection, strategy
certificate, historical performance claim or live-trading authority is produced.

```powershell
.\cli.ps1 portfolio replay --input C:\private\portfolio-replay.json
.\cli.ps1 portfolio replay-report --input C:\private\portfolio-replay.json
```

Supply exactly these top-level fields: `initial_cash`, `daily_loss_usd`, `slippage_bps`, `fee_bps`,
`max_quote_age_seconds`, `max_execution_delay_seconds`, and `frames`. All numeric inputs must be
explicit and finite; costs may be zero. A frame has exactly:

- `request`: a mixed allocation request. Its holdings must be empty because
  the replay builds its own book from initial cash; its capital is replaced with current virtual NAV.
- `quotes`: a symbol map, each with `bid`, `ask`, `observed_at`, `available_at`. Every earnings,
  held and pending asset needs a quote. Earnings-screen quotes must match the tape.
- `theses`: explicit symbol-to-boolean current thesis status for held and pending assets.
- `corporate_actions`: an empty list. Nonempty events reject the replay until adjustments exist.

Frames must be strictly chronological and within scheduled regular sessions. Allocation policy and
earnings thresholds are frozen across the run. Inputs and costs are bound into a reproducible hash.
Quote and risk provenance remains user-supplied and unverified.

### Execution and accounting contract

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

### What this does not establish

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
