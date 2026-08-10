# Changelog

## 0.9.0 - 2026-08-09

- Split batched quote observation from slower portfolio, position, and order reconciliation.
- Added configurable 0.25-5 second quote targets, 1-300 second completed decision bars, and 2-60 second account reconciliation.
- Coalesced overlapping quote reads so provider latency creates backpressure instead of request queues.
- Preserved independent quote-age, spread, open-order, 12-second cooldown, and orders-per-minute gates.
- Added immediate local open-order state after submission and cadence regression tests.
- Documented why this is retail low-latency automation rather than exchange-colocated HFT.

## 0.8.0 - 2026-08-09

- Added a paper-faithful first-half-hour QQQ signal that trades only the final half hour.
- Replaced unpurged fold boundaries with a configurable purged walk-forward gap.
- Added Probabilistic and Deflated Sharpe calculations, a persistent unique-trial ledger, and evidence-policy version 5.
- Added causal fixed-exposure, volatility-managed, and volatility-plus-SMA200 daily benchmarks to the Action Lab.
- Invalidated the legacy positive closing-momentum claim after correcting unintended overnight exposure.
- Expanded tests and primary-source research documentation; no strategy is promoted for live use.

## 0.7.0 - 2026-08-09

- Added an exact nine-action `(T,S)` command model with long-only inventory masks and an auditable offline Q-learning lab.
- Added a full common-history daily QQQ/TQQQ/SQQQ source, local caching, provenance, and a chronological 70/30 holdout.
- Corrected offline rewards to include causal close-to-next-close exposure and modeled transaction costs.
- Corrected replay hold duration to use elapsed time and made session-end virtual flattening the safe default.
- Added the Action Lab matrix, holdout action ledger, research receipts, tests, documentation, and restrained public social assets.
- Fixed GitHub packaging so CI uses the active runner Python when a local `.venv` is absent.

## 0.6.1 - 2026-08-09

- Added an interactive sandbox trade timeline with TQQQS/SQQQS price paths and every virtual fill.
- Distinguished buys, profitable sales, losing sales, and flat sales with both shape and color.
- Added hover details, marker-to-fill-ledger selection, a realized-sales summary, and synchronized replay cursors.
- Added a regression test that requires every virtual fill to appear on the chart.
- Published a readable static preview from the frozen 40-session closing-momentum benchmark.

## 0.6.0 - 2026-08-09

- Added a causal research-only strategy library for closing momentum, multi-horizon trend, opening-range breakout, and conservative signal agreement.
- Added strategy selection and parameter controls to the sandbox, plus same-dataset tournament comparison.
- Bound evidence certificates to the bar interval, exact strategy candidate, policy version, and tested live-risk envelope.
- Added trial-adjusted significance, ending-flat, exact-candidate, and 120-session data-breadth gates.
- Allowed risk-reducing sells through the full regular session while blocking new entries in the configured close window.
- Forced live-shadow decision settings to match the live EMA signal stream and recorded its fingerprint.
- Published the complete 40-session tournament, including negative candidates and the close-momentum candidate's failed promotion gates.

## 0.5.0 - 2026-08-09

- Added persisted, 30-day evidence certificates bound to the exact strategy fingerprint.
- Added data-recency, minimum-trade, profit-factor/expectancy, random-control, 3x-cost, and stronger walk-forward gates.
- Made Settings, live authorization, strategy start, and live-control visibility fail closed when evidence is absent, stale, or mismatched.
- Expanded evidence tables, receipts, tests, and public documentation for the new policy.
- Published the current 40-session negative baseline result instead of making a profitability claim.

## 0.4.0 - 2026-08-09

- Added research-first onboarding and a Getting Started workspace.
- Split broker access, real orders, remote market data, and the optional personal ledger into independent permissions.
- Added immediate revocation, credential-forget support, and redacted diagnostic export.
- Disabled remote community data by default and added per-session disclosure.
- Added application branding, version metadata, privacy/security/support policies, and public release automation.
- Reworked public documentation to remove personal account, tax, immigration, and balance assumptions.

## 0.3.0

- Added sandbox evidence lab, shadow execution, configurable costs, and local research-fund ledger.
