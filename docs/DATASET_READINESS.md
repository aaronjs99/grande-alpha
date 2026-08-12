# Observed-data readiness and sealed-holdout procedure

## Runtime-observation traces are a separate schema

Generic OHLCV CSV readiness is not runtime-observation parity. Exact replay requires GRANDE Alpha's
synchronized venue-quote schema: three QQQ/TQQQ/SQQQ quote rows per recorder batch, venue timestamps,
QQQ bid/ask-mid bar construction, and the first later causal target bid/ask batch. The runtime-trace
importer reads SQLite with `mode=ro`, hashes the exact source rows, and can emit a manifest template
that defaults every rights attestation to false. The user must verify the provider/product terms and
complete those attestations; a source label is never enough.

The trace contains no volume. This is recorded explicitly rather than imputed. It can close a
bar/signal/fill-clock engineering blocker, but one day cannot satisfy the 141-session breadth needed
to reserve 120 development sessions, one purge session, and a 20-session sealed final holdout.

This procedure qualifies a data **input** for GRANDE Alpha. It does not show that a strategy is
profitable, create a live-review certificate, authorize an order, or establish that a market-data
license is legally sufficient. The audit is local and read-only: it does not call a broker, register
a strategy trial, reserve a final holdout, or evaluate a final holdout.

## What the exact 5-second dataset must contain

Use one CSV with this header, in any row order:

```csv
timestamp,symbol,open,high,low,close,volume,market_hours
```

The row grain is one symbol and one bar-start timestamp. Requirements:

- `timestamp`: ISO 8601 with an explicit UTC offset; UTC (`Z` or `+00:00`) is the manifest standard.
- `symbol`: exactly `QQQ`, `TQQQ`, or `SQQQ`.
- `open,high,low,close`: finite positive numbers with `low <= open/close <= high`.
- `volume`: finite and nonnegative. Zero volume is reported separately because it may be legitimate
  or may indicate a bad export.
- `market_hours`: one consistent value: `regular_hours`, `extended_hours`, or `all_day_hours`.
- Composite key `(timestamp,symbol)` is unique.
- Every timestamp contains exactly one row for all three symbols. GRANDE Alpha aligns on their
  timestamp intersection; the readiness audit fails instead of silently accepting dropped rows.
- The declared interval is the **actual** bar interval. One-minute or daily prices must never be
  forward-filled, interpolated, repeated, or relabelled as 5-second observations.

For the default regular-hours 5-second target, a normal 9:30 a.m.-4:00 p.m. Eastern session has
4,680 timestamps per symbol; a scheduled 1:00 p.m. early close has 2,520. The current policy requires
at least **141 complete sessions total**: 120 development sessions, one purge session, and a later
20-session one-use final holdout. Every scheduled U.S. equity session between the first and last
manifest timestamps must be present; a cherry-picked export that omits an entire intervening trading
day fails readiness even if the remaining 141 days are individually complete.

## Provenance manifest v1

Print the exact JSON template without creating or modifying evidence:

```powershell
.\.venv\Scripts\python.exe -m grande_alpha.cli data manifest-template --target-interval 5s
```

Save the output beside the CSV as, for example, `qqq-tqqq-sqqq-5s.manifest.json`, then replace every
placeholder. Do not put API keys, OAuth tokens, account numbers, passwords, or private contract text
in the manifest.

Required fields and meanings:

| Field | Requirement |
|---|---|
| `manifest_version` | Integer `1` |
| `dataset_id` | Stable user-assigned identifier |
| `created_at` | Aware ISO 8601 manifest timestamp |
| `provider`, `provider_product` | Legal source and exact licensed/export product |
| `acquisition_method` | API/export method, with no credential |
| `license_reference` | Public terms URL or local agreement name, with no secret |
| `license_reviewed_by_user` | `true` only after the user actually reviews the applicable terms |
| `research_use_permitted` | `true` only if the reviewed terms permit this research use |
| `automated_strategy_research_permitted` | `true` only if the terms permit this modeling use |
| `redistribution_permitted` | Accurate boolean; `false` is acceptable and is the safe default |
| `observed_data` | `true` |
| `synthetic_or_interpolated` | `false` |
| `symbols` | Exactly `["QQQ","TQQQ","SQQQ"]` |
| `bar_interval` | Exact output cadence, normally `5s` |
| `source_resolution_seconds` | Positive and no coarser than the output interval; 5 or less for 5-second bars |
| `construction_method` | `provider_native`, `aggregated_from_trades`, `aggregated_from_quotes`, or `aggregated_from_nbbo` |
| `contains_upsampled_rows` | `false` |
| `timestamp_timezone`, `timestamp_semantics` | `UTC` and `bar_start` |
| `market_hours` | Must match every CSV row and the tested execution session |
| `start`, `end` | Must exactly match the first and last aligned bar timestamps |
| `price_adjustment` | `unadjusted`, `split_adjusted`, or `split_and_dividend_adjusted` |
| `corporate_action_policy` | Nonempty explanation of price and volume treatment |
| `csv_sha256` | SHA-256 of the raw CSV bytes |
| `dataset_hash` | GRANDE Alpha canonical hash of aligned OHLCV bars |
| `row_count` | Exact number of CSV data rows, excluding the header |

The license fields are user attestations, not legal advice or independent verification by the app.
Keep the source agreement or terms snapshot outside the repository if redistribution is prohibited.

## Read-only qualification command

First inventory the existing local caches and evidence ledger:

```powershell
.\.venv\Scripts\python.exe -m grande_alpha.cli data audit --target-interval 5s --width 150
```

Then audit the supplied file and manifest:

```powershell
.\.venv\Scripts\python.exe -m grande_alpha.cli data audit `
  --csv "C:\data\qqq-tqqq-sqqq-5s.csv" `
  --interval 5s `
  --target-interval 5s `
  --manifest "C:\data\qqq-tqqq-sqqq-5s.manifest.json" `
  --width 150
```

An exit code of `0` and `INPUT READY` mean only that all input checks passed. An exit code of `1`
means the input is not ready; it does not alter the file or evidence ledger. `--json` produces a
machine-readable report. The audit opens SQLite with `mode=ro` and `query_only`, and reports explicit
zero broker calls and zero holdout actions. It also reports locally collected quote/bar counts and
time ranges as forward-collection progress. Those runtime traces are not an eligible HistoricalBundle
until aligned OHLCV bars for all three symbols, complete sessions, exact construction, and manifest-
bound provenance are established.

## One-use final-holdout checklist

1. Preserve the original CSV and manifest unchanged. Record both hashes and keep a recoverable copy.
2. Use only development history to choose features, strategy, parameters, cost model, and risk limits.
   Do not plot, rank, or tune against the last 20 sessions intended for final evaluation.
3. Keep one full session between development and holdout as the purge/embargo period.
4. Run `data audit` first. It performs quality/provenance checks only and leaves the holdout ledger
   unchanged.
5. Freeze one candidate configuration and its exact strategy fingerprint before a final Evidence Lab
   run. Changing cadence, sizing, session, order type, costs, signal, exit, or risk settings creates a
   different candidate.
6. Run the Evidence Lab with the full audited file. The service reserves the later 20-session block
   before development evaluation. If all development gates pass, it freezes the selected fingerprint,
   claims the holdout before calculation, evaluates it once at 3x modeled costs, and records the result.
7. A failed or interrupted claimed evaluation is not reusable. Do not edit SQLite or delete a receipt.
   A different candidate requires genuinely later untouched market data and a new holdout.
8. Passing the final holdout still requires every other current gate and monitored forward shadow.
   It permits a separate live review only; it never guarantees profit or starts trading.

## Current local snapshot (2026-08-11)

The read-only audit found:

| Local source | Native interval | Sessions | Final bar | Exact 5s input? |
|---|---:|---:|---:|---:|
| Unsupported community daily cache | 1 day | 4,147 | 2026-08-07 | No |
| Unsupported community intraday cache | 5 minutes | 40 | 2026-08-07 | No |
| Unsupported community intraday cache | 1 minute | 5 | 2026-08-07 | No |

All three caches passed their stored canonical content hashes. They remain useful for exploratory
daily/5-minute/1-minute research at their native cadence, but none can support the exact 5-second
runtime claim. The local ledger contained 81 registered trials on two deterministic scenario hashes,
five `SHADOW_ONLY` promotion receipts, and **zero** reserved/frozen/evaluating/consumed holdouts.
These values are a dated snapshot; rerun `data audit` for current state.
