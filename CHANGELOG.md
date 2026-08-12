# Changelog

## 0.15.0 - 2026-08-11

- Added session-scoped live-pilot machinery with typed, non-persistent authority bound to one
  Agentic account, the exact TQQQ/SQQQ ticker set, order route, candidate fingerprint,
  Eastern-day expiry, and explicit order, turnover, exposure, loss, rate, spread, and quote-age
  limits.
- Added fresh account, position, order, and venue-timestamp quote preflight; durable intent
  provenance before broker placement; conservative restart restoration of daily placement usage;
  and hash-chained authority-action receipts.
- Added visible pause and revoke controls while retaining fail-closed reconciliation, unknown-order,
  stale-data, settings-change, and expired-authority locks.
- Added a unified Live Readiness checklist in the GUI and CLI with explicit app, research, user,
  and external-review owners; a structurally read-only safe-refresh path; and reversible bounded-pilot
  route previews that do not save, authorize, or place orders.
- Made every autonomous exit one-shot, revoking authority after one known placement response so
  stale position data cannot trigger a duplicate sell. The daily-loss transition is additionally
  liquidation-only: it can only sell existing leveraged inventory and never add exposure. Exact
  inventory-reducing exits may exceed the entry-notional cap only up to fresh reconciled sellable
  inventory, while buy-side and total risk controls remain unchanged.
- Added conservative broker order/inventory reconciliation for cumulative and partial fills,
  placement-restart usage, delayed inventory updates, and retry-safe exits without inventing a fill
  timestamp; the unresolved provider-observation mismatches remain explicit parity blockers.
- Restricted live authorization/start to the regular-session entry window and atomically discarded
  all pre-start/premarket signal state before the live warm-up begins.
- Added a recurring U.S. cash-equity holiday and scheduled early-close calendar, while continuing to
  fail closed because emergency closures, venue outages, and symbol halts need current provider data.
- Hardened Morning Check behind a structural read-only broker facade, exact-one Agentic-account
  selection, exact fresh venue-quote validation, and conservative unknown-order-state handling.
- Added a shared immutable candidate execution/sizing contract for replay, live shadow, and bounded
  runtime order preparation, without presenting simulated execution assumptions as broker facts.
- Added a query-only historical-data audit, exact CSV/provenance-manifest contract, native-cadence
  checks that reject 1-minute/daily relabeling as 5-second data, and a one-use holdout checklist.
  Evidence policy v11 now binds this provenance to holdout and promotion receipts and ignores
  human-readable source labels when deciding historical-source eligibility. It requires 120
  complete development sessions, one purge session, and 20 complete later holdout sessions; rejects
  omitted exchange sessions, overlapping holdouts, future/stale timestamps, and mutable CSV reads;
  and applies durable trial counts to trial-adjusted significance.
- Made scheduled shadow apply an audited, process-local regular-hours read-only route so a saved
  normal-app 24-hour/limit/GTC preference cannot prevent the next observation run; no setting is
  persisted and the broker-write facade remains active.
- Kept deterministic CASH / hold as the normal and scheduled champion. The non-cash runtime-sizing
  parity flag remains false because the complete runtime execution-parity assessment is still blocked;
  no strategy currently receives live authority, and this release makes
  no profitability or investment-performance guarantee.

## 0.14.0 - 2026-08-11

- Made deterministic CASH / hold the normal and scheduled runtime champion after every existing
  intraday family produced negative purged out-of-sample returns under three-times modeled costs.
- Added deliberate runtime-strategy selection in Settings, visible champion labels, safe strategy
  hot-reload, and migration of older settings to the cash default.
- Kept research strategies available for explicit shadow experiments without granting real-order
  authority or making a profitability claim.
- Aligned controller-driven shadow fills with replay/live causality: a completed-bar decision uses
  the first available quote of the next bar, with no extra-bar delay or duplicate fill.
- Timestamped a skewed accepted quote batch at its latest consumed venue observation so a virtual
  fill can never be recorded before the TQQQ/SQQQ quote that priced it.
- Made every signal or bar-setting change atomically replace both the strategy and bar builder,
  discard any partial bucket, and retain only the latest-observation duplicate guard.
- Upgraded evidence policy to version 9, binding all material execution, sizing, risk, lifecycle,
  and random-seed fields into the candidate fingerprint.
- Made cost stress scale static and volatility-driven spread, slippage, and commission components.
- Rejected evidence that depends on a simulator-only, failure-bypassing forced close.
- Added an explicit runtime-sizing-parity gate and storage lock; non-cash candidates cannot receive
  live-review eligibility until replay, shadow, and live use one certified sizing contract.
- Published the purged, cost-stressed champion tournament while leaving its terminal five-session
  block untouched for a future frozen candidate.

## 0.13.0 - 2026-08-11

- Added an opt-in, per-user Windows task that launches GRANDE Alpha at 6:20 AM local time on
  weekdays, can wake an already logged-in sleeping computer, never stores a Windows password,
  never catches up a missed start, and prevents overlapping runs.
- Added a dedicated `--auto-shadow` runtime with a broker-level read-only facade that rejects order
  review, placement, and cancellation regardless of UI or controller state.
- Required cached noninteractive OAuth, exactly one active Agentic account, no open Agentic orders,
  no real TQQQ/SQQQ position, a valid reconciled portfolio, and exact fresh venue-timestamped
  QQQ/TQQQ/SQQQ quotes before an automatic shadow run can begin.
- Connected at the scheduled pre-open time, waited until 9:30 AM ET, discarded premarket strategy
  state, and failed closed if readiness was not achieved by 9:35 AM ET.
- Stopped virtual execution and disconnected without broker writes after data or account transport
  failure, and automatically ended each scheduled run at the 4:00 PM ET regular-session close.
- Stopped manufacturing a current timestamp for provider quotes that omit venue timestamps; those
  rows are now unusable for automatic freshness checks.

## 0.12.0 - 2026-08-10

- Added a one-use, purged final holdout lifecycle that reserves the unseen block before candidate
  work, freezes the exact strategy fingerprint, claims the block before reading it, and permanently
  consumes or invalidates it after one evaluation.
- Required every live-review certificate to include a current-policy, consumed holdout for the exact
  strategy and to pass the holdout after three-times modeled execution costs.
- Modeled cash-account T+1 settlement in replay and live shadow: sale proceeds remain equity but
  cannot fund another purchase until the next observed trading session.
- Forced cash Agentic accounts to use the T+1 model for shadow and real-order eligibility, while
  retaining broker-reported buying power as the authority for any future submitted order.
- Added a read-only `Morning Check.cmd` preflight that verifies the local app and Robinhood read path
  without reviewing, placing, modifying, or canceling an order.
- Revoked live authority on every runtime-settings change and revalidated the exact current evidence
  immediately before each automatic decision, broker review, and final placement call.
- Rejected non-finite or malformed grants, quotes, order sizes, portfolio values, exposure values, and
  stored risk envelopes at the model, risk-engine, and evidence-storage trust boundaries.
- Rebuilt the live signal strategy and reset its warm-up whenever fingerprinted signal settings change,
  keeping the running implementation aligned with its evidence certificate.
- Made the connected account card identify cash accounts and added settlement warnings to the
  live-session permission flow, sandbox controls, glossary, runbooks, and product documentation.

## 0.11.0 - 2026-08-10

- Added user-selectable regular, extended, and Robinhood 24 Hour Market routes in Settings, the
  per-session authority dialog, and the isolated sandbox.
- Enforced the Trading MCP matrix: extended and overnight routes are limit-only; market orders are
  regular-hours GFD; automatic limits use whole-share quantities and explicit prices.
- Derived buy limits from ask plus a configurable offset and sell limits from bid minus that offset,
  with cent-safe rounding and fail-closed handling when a whole share cannot fit the grant.
- Bound session, order type, time in force, and limit offset into evidence-policy version 7 and added
  a complete-session data-coverage gate.
- Added session-aware risk windows and sandbox grouping, pre/post-market community-data requests,
  and a hard refusal to claim complete 24-hour evidence from that incomplete source.
- Added live 24-hour symbol-eligibility checks and preserved pending-order deduplication, broker
  review, bounded grants, and the STOP + CANCEL path.
- Moved the authenticated MCP transport, tool calls, and teardown into one dedicated async owner
  task so connect/disconnect no longer crosses AnyIO cancel-scope task boundaries.
- Bound Desktop and Start-menu shortcuts to the running app's Windows identity, added a readiness
  check, and verified that a branded taskbar pin survives close and relaunch.
- Added dashed-underlined contextual terms across the dashboard, Settings, Sandbox, and live-session
  review, plus hover/click explanations, accessible descriptions, evidence-row help, and an F1 glossary.
- Made disabled Settings saves explain the exact blocker and how to preserve broker-only or research
  changes without attempting an ineligible live-order permission.
- Made every table column manually adjustable, added header fit/reset controls, and gave compact
  fields such as Status narrower defaults so evidence observations and requirements remain usable.
- Added selected-gate explanations and defensible next actions, while clarifying that a pass count is
  not a progress score because every independent promotion gate is mandatory.
- Added `grande-alpha-cli` with shared sandbox and Evidence Lab execution, local status, evidence,
  saved-run, receipt, glossary, wrapping-table, adjustable-width, and JSON commands.

## 0.10.0 - 2026-08-10

- Made the analysis and trade clocks explicit: the default completed analysis bar is 5 seconds and
  a pair-action decision occurs every 3 bars, so nominal `t_analysis=5s < t_trade=15s`.
- Expressed each live target transition in the exact nine-action `(T,S)` command vocabulary and
  recorded the selected command, source analysis, inventory, target, cadence, and state feasibility.
- Added a visible pair-action status card and separate analysis/trade cadence controls.
- Added a configurable research decision stride and bound it into evidence fingerprints, invalidating
  older timing-mismatched certificates under evidence policy version 6.
- Preserved long-only inventory, mutual-exclusion, sells-before-buys, open-order, cooldown, broker
  review, risk-envelope, and shadow/evidence gates; no failed research result was promoted.

## 0.9.3 - 2026-08-10

- Stored oversized Robinhood OAuth material as integrity-verified chunks in Windows Credential Manager.
- Preserved direct legacy credentials and migrated them without deleting the recoverable originals.
- Disabled an unsupported MCP session-termination request that produced a false provider 400 warning.
- Added complete File, View, Broker, Research, Safety, and Help desktop menus with keyboard shortcuts.
- Redesigned Settings & Permissions into a scrollable, accessible account-scope and capability review.
- Registered a stable Windows app identity so the GRANDE Alpha logo is used for the window and taskbar.

## 0.9.2 - 2026-08-10

- Added a local Desktop and Start Menu installer using the trusted PowerShell/Python launch path.
- Added an explicit read-only Robinhood OAuth diagnostic covering accounts, portfolio response, quotes, positions, and orders.
- Kept all order review, placement, and cancellation methods outside the diagnostic path.

## 0.9.1 - 2026-08-09

- Added a readiness doctor and native main-window cadence regression test.
- Verified the signed-Python source launcher under Windows Smart App Control.
- Labeled PyInstaller output as an unsigned candidate after Code Integrity correctly blocked it.
- Added a runnable Windows source release bundle alongside the unsigned binary candidate.
- Moved source-bundle environments to a short managed runtime path to avoid PySide6 long-path installation failures.
- Documented the trusted Authenticode signing gate instead of presenting an unsigned executable as public-ready.

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
