# Evidence lab and promotion gates

## Exact runtime-observation evidence

Evidence-policy v13 requires the canonical **Exact runtime observation schema** gate. A bounded live
candidate must be evaluated from a provenance-bound GRANDE Alpha quote trace whose synchronized
QQQ/TQQQ/SQQQ venue observations reproduce the runtime path: QQQ bid/ask mids enter the same
`BarBuilder`, and the first later accepted quote batch supplies the causal timestamp plus TQQQ/SQQQ
bid/ask used by virtual execution. A generic OHLCV CSV remains useful for research but cannot pass
this gate merely by being labeled runtime-equivalent.

The importer opens the local SQLite trace read-only, content-hashes every atomically recorded v2
batch, excludes legacy unbound rows, rejects incomplete/interleaved/skewed triples, filters
observations outside the declared exchange session, and binds each signal-pipeline reset with a
durable stream ID. One stream may not span sessions: scheduled daily runs must create a new stream,
which is the exact production reset boundary. Quote traces do not contain volume. Derived bars therefore
record zero volume; no volume-capacity or participation claim can be made from them.

Each atomic batch also records how it was accepted. Only `exact_execution_quotes` validator v2
batches with one consistent, finite age/skew envelope (no more than 8 seconds old and 5 seconds
skew, with skew never exceeding age) and explicit venue bid and ask clocks enter exact replay.
Policy-v12 receipts and validator-v1 batches are stale and cannot activate live review. Ordinary passive connected refreshes are
stored as `passive_unvalidated` and excluded. A stale passive snapshot cannot become eligible later
through a rights manifest or source label.

The evidence service locks the whole run to one replay family. An eligible runtime trace routes the
base candidate, every neighboring-parameter trial, 1x/2x/3x cost stress, seeded random-entry
control, every purged walk-forward train/test fold, and the one-use final holdout through the exact
causal engine. Those executions are summarized into the same canonical `SandboxResult` metrics and
carry `runtime_observation_replay=true`. If any child partition loses an exact quote observation the
run fails; it never falls back to generic OHLCV. Ordinary OHLCV continues through the generic
sandbox and keeps the marker false, so a label or source-name change cannot pass the schema gate.

Multi-session exact replay follows scheduled auto-shadow lifecycle semantics. Signal state, virtual
capital, T+1 proceeds, and the seeded execution RNG start clean each session. Per-session P/L is
then aggregated onto one canonical starting-capital curve for evidence statistics. A production-style
virtual close is reproduced only when the recorded causal quote reaches the declared session close;
a partial trace cannot invent a closing fill. Any such failure-bypassing daily flatten is counted by
the ending-flat and holdout gates. This exact evidence path still does not set the separate global
runtime-sizing parity certification flag and makes no profitability claim.

Before any Evidence Lab run, use the dedicated range-bound audit and template commands:

```powershell
& ".\GRANDE Alpha CLI.cmd" data runtime-trace audit `
  --database "$env:LOCALAPPDATA\GRANDEAlpha\grande_alpha.db" `
  --bar-seconds 5 --session regular_hours `
  --start YYYY-MM-DD --end YYYY-MM-DD --manifest "C:\data\runtime.manifest.json"
```

Audit and template commands are query-only and cannot reserve or evaluate a holdout. The explicit
`evidence run --source runtime-trace` command requires the same range and attested manifest, rejects
input that is not ready before opening the evidence store, and then uses the normal one-use lifecycle.

The evidence lab is designed to make a promising backtest harder to fool. Its strongest outcome,
`LIVE_REVIEW_ELIGIBLE`, creates a local, 30-day certificate for the exact strategy fingerprint.
That certificate only makes the separate live-risk review available; it does not authorize an
order or predict future profit. The normal outcome is `SHADOW_ONLY`.

## Tests produced

- **Preset comparison:** every preset runs on the same immutable dataset.
- **Parameter sensitivity:** strategy-appropriate neighboring parameters show whether the result
  is broad or a single lucky point.
- **Cost stress:** the candidate reruns at 1x, 2x, and 3x slippage, commissions, and both
  the static and volatility-driven spread components.
- **Random-entry control:** seeded random direction/entry trials provide a basic luck benchmark.
- **Purged walk-forward:** each fold selects a configuration using earlier training sessions,
  leaves a configurable session gap, and measures it only on later test sessions.
- **Profit concentration:** checks whether one day accounts for most positive daily P/L.
- **Trial ledger:** unique candidate fingerprints are committed by dataset before promotion is
  evaluated, so previously registered trials cannot disappear from the reported search count.
- **Trial-adjusted significance:** applies both a Bonferroni familywise correction and the
  Deflated Sharpe Ratio's selection-bias, skew, and kurtosis adjustment.
- **One-use final holdout:** the current evidence policy reserves a later chronological block before
  candidate evaluation, freezes it to the selected strategy fingerprint, claims it before reading
  its result, and records that result permanently after one evaluation.

## Current gates

| Gate | Requirement |
|---|---|
| Historical source | Observed or imported market history; synthetic scenarios are ineligible |
| Trading-session coverage | Dataset covers the complete regular, extended, or 24-hour session selected by the strategy |
| Data breadth | At least 141 total market sessions: 120 development, one purge, and 20 final holdout sessions |
| Data recency | Final observation no more than 30 days old |
| Data integrity | Hash-valid development data with zero omitted exchange sessions, zero duplicate/missing intraday intervals, and at least 95% complete selected sessions; the sealed holdout is checked separately under the same quality rule |
| Runtime sizing parity | Replay, shadow, and live share the exact certified execution contract. Entry sizing, loss semantics, completed-bar cadence, distinct filled-entry counting, and holding clocks now share durable provider execution identity/time semantics. Non-cash certification remains blocked by market-observation construction, replay-versus-broker fill economics/timing, the autonomous exit lifecycle, and the provider's current per-order review/confirmation contract. Malformed or missing provider execution provenance fails closed. Cash passes this gate only with zero fills/exposure, then still fails trade-sample and profit gates. |
| Parameter stability | At least half of neighboring configurations profitable |
| Cost stress | Positive P/L at 3x modeled costs |
| Closed-trade sample | At least 30 after-cost round trips |
| After-cost quality | Profit factor at least 1.20 and positive expectancy |
| Random-entry control | Strategy at or above the 75th percentile of seeded random trials |
| Trial-adjusted significance | One-sided Bonferroni-adjusted daily-P/L p-value at most 0.05 |
| Deflated Sharpe | At least 95% probability after registered-trial and non-normality adjustment |
| Profit concentration | No single day over 50% of positive daily P/L |
| Drawdown | No more than 5% in the research configuration |
| Ending flat | No virtual position remains open, and the result did not rely on the simulator's failure-bypassing forced close |
| Exact candidate identity | Every training fold selected the exact configuration being certified |
| Walk-forward | At least five folds, 60% positive test folds, 20 out-of-sample trades, median profit factor 1.10, and positive median expectancy |
| Sealed final holdout | The frozen candidate passes one later, purged, one-use holdout at 3x modeled costs with positive P/L, at least 5 round trips, profit factor at least 1.10, positive expectancy, drawdown no more than 5%, and an ending-flat result that did not rely on the simulator's failure-bypassing forced close |

All gates must pass simultaneously. Every pass and failure is stored in the local audit database.
Changing a fingerprinted signal, exit setting, bar interval, decision stride, execution session,
order type, time in force, limit offset, or policy version invalidates the
certificate. A requested live grant cannot exceed the notional, exposure, loss, trade-rate, or
spread envelope tested by the certificate, and a certificate older than 30 days is ineligible. A pass does not estimate the probability of future profit,
validate the market-data license, account for tax/settlement restrictions, or authorize real money.
Before any live review, preserve a final dataset period that was not used to invent, select, or tune
the strategy.

## Current-policy final-holdout lifecycle

The seal is an auditable database lifecycle, not encryption. The app records the full dataset hash,
development hash and quality, holdout hash/dates and quality, policy version, source-provenance hash,
and selected strategy fingerprint. Any date overlap with any prior holdout—including consumed or
invalid attempts—is rejected; exact idempotency applies only while the same record is still reserved.

```text
RESERVED -> FROZEN -> EVALUATING -> CONSUMED
```

- `RESERVED` fixes the later chronological block before candidate evaluation begins. If the
  development-only gates fail, the block remains reserved and unread rather than being wasted.
- `FROZEN` binds that block to exactly one selected strategy fingerprint, and occurs only after all
  non-holdout gates pass.
- `EVALUATING` is claimed atomically before the holdout result is calculated, so a crash or bad
  result cannot make the same block look unused.
- `CONSUMED` stores the metrics whether the gate passes or fails. The same dataset/date block cannot
  be reserved again under another candidate or policy version.

At the live-authority boundary, storage independently requires the complete canonical gate set,
recomputes the final-holdout thresholds from the consumed metrics, binds the full dataset and final
timestamp to the holdout record, and permits at most one promotion receipt for that holdout. Caller-
supplied `passed` labels alone are never sufficient.

Changing a parameter after the reveal creates a new candidate; it does not justify another attempt
on the consumed block. A new evaluation requires genuinely later untouched data and a new sealed
holdout. Walk-forward folds and neighboring-parameter tests remain development evidence and do not
replace this final one-use test.

## Avoiding false evidence

- Do not choose the best configuration and then describe its training return as out-of-sample.
- Do not inspect, retune on, or rerun the final holdout. Under the current policy, a claimed holdout is used
  once and remains consumed even when it fails.
- Do not ignore rejected fills, open ending positions, costs, negative days, or zero-trade folds.
- Do not compare two configurations on different hashes.
- Record failed experiments as well as successful ones.
