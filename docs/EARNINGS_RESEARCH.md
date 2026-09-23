# Earnings research: PEAD candidate screen

Status: **research-only screening implemented**. No backtest, portfolio construction, live stock
execution, LLM decision stage, or profitable strategy is implied. The runtime's TQQQ/SQQQ order
allowlist is unchanged. A screen candidate cannot become a live order or evidence certificate.

Post-earnings-announcement drift is a research hypothesis worth evaluating, not an established
best trading marker. Historical research finds that costs can materially reduce apparent PEAD
profits; see [Ng, Rusticus and Verdi (2008)](https://experts.umn.edu/en/publications/implications-of-transaction-costs-for-the-post-earnings-announcem/)
and [Chordia et al. (2009)](https://business.columbia.edu/faculty/research/liquidity-and-post-earnings-announcement-drift).
Those studies are not evidence that this implementation will earn money now.

## Commands and input contract

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

## Exact calculations

- Surprise: `(actual_eps - consensus_eps) / reference_price * 10000`.
- Post-announcement momentum: `(((bid + ask) / 2) / post_price - 1) * 10000`.
- Spread: `(ask - bid) / ((bid + ask) / 2) * 10000`.

Surprise is **price-scaled EPS surprise**, not percentage EPS growth or standardized unexpected
earnings (SUE). A zero/negative consensus does not create a division-by-zero or reverse the sign.
Momentum measures movement after the supplied post-announcement baseline, not the initial gap.
This version screens positive surprise and positive subsequent momentum; it does not short misses.

## Output and remaining qualification

Each event is `RESEARCH_CANDIDATE`, `NO_CANDIDATE`, or `INVALID_INPUT`. Failed thresholds and invalid
chronology remain visible. Invalid rows cause exit status 2, even when other rows are valid.
The report includes the canonical input SHA-256 and explicitly reports unverified user-supplied
provenance, no live eligibility, no authority, and no backtest. A source ID is not license evidence.

Required before evaluating deployment: point-in-time expectations and release times; historical
universe including delistings; corporate-action consistency; chronological training/holdout splits;
portfolio capital and settlement accounting; entry/exit rules; commissions, spreads, slippage and
market impact; missing-data and restatement handling; benchmark comparisons; and provider-qualified
individual-stock execution. Never optimize a threshold on the same observations used to claim a result.
