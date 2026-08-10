# Changelog

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
