# Activation checklist: what the app can do and what you must do

GRANDE Alpha now presents this same checklist in **Live Readiness** and in the terminal with:

```powershell
& ".\GRANDE Alpha CLI.cmd" activation --width 150
```

The checklist is fail-closed. It explains a lock; it does not offer a bypass. The activation assistant
cannot grant, schedule, review, place, or cancel orders. Shared account, route, and capability checks can
expose the attended supervised review; a fully passing checklist is required only for evidence-gated
autonomous review. Neither route predicts or guarantees profit.

## First: identify which process you opened

- **Scheduled auto-shadow** is structurally read-only. Its broker facade blocks order review,
  placement, and cancellation. It cannot turn itself into live trading, even if every other item later
  passes.
- **Normal GRANDE Alpha** has an attended supervised experimental path with fresh confirmation for
  every exact reviewed order and a separate autonomous path that remains locked until exact evidence
  and runtime conditions pass. It still starts without a saved money-moving grant.

If the title or checklist says `AUTO-SHADOW PROCESS — STRUCTURALLY READ-ONLY`, let it collect virtual
observations or close it. Do not look for a live checkbox in that process.

## Exact procedure

### 1. Open Live Readiness — app does this

Launch normal GRANDE Alpha, then open **Live Readiness**. The table shows:

- `APP CHECK`: the app can safely recheck this with broker reads;
- `APP GATE`: software/evidence must prove it, and no checkbox can override it;
- `YOU`: a deliberate consent, account, or money decision;
- `RESEARCH`: new observed evidence is required;
- `EXTERNAL REVIEW`: a qualified person or provider must decide it outside the app.

Select the first blocked row and click **Open selected next step**. The app routes you to Settings,
Research Sandbox, broker connection, or a precise manual instruction.

### 2. Provide lawful observed data — you provide it; app validates it

The app cannot invent qualifying history. In **Research Sandbox**:

1. Choose an observed source that you are permitted to use. A deterministic scenario is for software
   testing only and can never qualify.
2. For CSV import, provide aligned QQQ, TQQQ, and SQQQ observations for the exact interval and trading
   session being evaluated.
3. Cover at least 141 complete market sessions: 120 development, one purge, and 20 later one-use
   holdout sessions. Keep the final observation within the evidence recency limit.
4. Run data-integrity checks. Fix duplicated, missing, misaligned, or incomplete source records rather
   than interpolating better performance.

### 3. Run Evidence Lab — app computes it; evidence must earn the result

Use **Research Sandbox → Walk-forward & gates → Run Evidence Lab**. The app runs replay, modeled
execution costs, parameter-neighborhood checks, random-entry control, trial adjustment, deflated
Sharpe, concentration, drawdown, ending-flat, sealed holdout, and walk-forward checks.

For every failed row, select it to see the exact next action. Do not lower thresholds, erase prior
trials, edit receipts, or choose unrealistic costs to make a result pass. More data is useful only when
it is genuinely new and relevant; it cannot rescue a false hypothesis by itself.

### 4. Establish replay/runtime parity — engineering and app tests do this

The live runtime must use the same strategy, sizing, cadence, settlement assumptions, route, and order
construction that produced the evidence receipt. `RUNTIME_SIZING_PARITY_CERTIFIED` remains false until
automated parity tests and review prove this. Flipping the constant is not certification and invalidates
the safety case.

This is repository engineering work Codex can help implement and test. After any parity or strategy
change, the exact candidate must be reevaluated from unchanged source data and a valid holdout.

### 5. Accumulate forward shadow evidence — app records it; elapsed markets supply it

Use **Start Live Shadow** or the scheduled shadow task. The app records live observations and virtual
fills without sending orders. Keep the strategy fingerprint unchanged during the monitored period and
review after-cost performance, drawdown, data gaps, and every receipt.

Neither Codex nor a GPU can compress future market sessions into genuine forward evidence. Shadow
results can reject a candidate; they cannot guarantee that later real trades will profit.

### 6. Complete external reviews — each operator does this outside the app

Account eligibility and the legal, tax, employment, residency, sanctions, and business rules that
apply to trading software vary by user, account, and jurisdiction. Before enabling real orders, each
operator must review the exact facts with appropriately qualified professionals and the broker. The
app cannot convert a checkbox or attestation into legal, tax, employment, residency, account, or
product-distribution clearance.

The **Jurisdiction & account suitability** responsibility is deliberately shown **outside** the
pass-count table because GRANDE Alpha cannot inspect or certify it. The notice does not infer a user's
location, citizenship, visa, employment, tax, or business status, and it does not ask the user to put
sensitive status details into the app. Distributors can add official local resources to the UI by
setting `GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS` to a JSON array of HTTPS links, for example:

```powershell
$env:GRANDE_ALPHA_EXTERNAL_GUIDANCE_LINKS = '[{"label":"Local regulator guidance","url":"https://regulator.example/guidance"}]'
```

Only HTTPS links with safe labels are shown. Configured links are references, not endorsements or a
substitute for individualized professional advice.

For public distribution involving Robinhood connectivity, obtain written Robinhood approval covering
the intended users, product, order flow, branding, and data handling. Review Robinhood's
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/).

Copy-paste draft for Robinhood Support (review and personalize before sending):

> Subject: Written scope confirmation for local Agentic Trading MCP application
>
> Hello, I am requesting written confirmation of the permitted scope for a public desktop application.
> It connects through the official Agentic Trading MCP, reads account/position/order and
> QQQ/TQQQ/SQQQ quote data, and—only after explicit same-day authorization—could submit bounded orders
> for an authorized account holder. Please confirm which private-use and public-distribution scenarios
> are permitted under the current Agentic Trading and API terms, whether a separate written agreement
> is required before publishing or distributing the application to other users,
> and what approval process covers order flow, branding, OAuth/data handling, support duties, and use
> of Robinhood or Agentic Trading names. I will not represent the application as approved or expose
> public Robinhood connectivity without your written confirmation.

Do not include OAuth tokens, account numbers, screenshots containing identifiers, or other secrets in
the first request.

### 7. Run the read-only broker preflight — app checks; you complete consent and verify

Run:

```powershell
.\Morning Check.cmd
```

Then in normal GRANDE Alpha:

1. Enable **Connect Robinhood broker data** in **Settings & Permissions** and save.
2. Click **Connect Robinhood** and complete Robinhood's browser consent yourself.
3. Open **Live Readiness** and click **Run safe checks** during regular market hours.
4. Independently verify the masked Agentic account, balances, positions, and orders against Robinhood.

Safe checks refresh account, position, order, and exact QQQ/TQQQ/SQQQ quote truth. They do not review,
place, or cancel an order. **Run safe checks** is disabled and refuses before refreshing whenever a
live grant exists or the strategy is running; revoke authority and stop first. This keeps the
read-only helper separate from live-state cleanup behavior. The app requires exactly one active
Agentic account for its app views and does not select the regular investing account for app orders.

### 8. Resolve inventory or order blockers — you decide; app verifies

- If TQQQ/SQQQ inventory exists, decide in Robinhood whether it should remain. The separately reviewed
  **Flatten Position** flow is a real sell and is not part of safe checks.
- If a GRANDE-owned working order exists, use **STOP + CANCEL**, review the exact count/details, and
  explicitly confirm that scope. Manual or unrelated Agentic-account orders are untouched. An order
  already pending cancellation is disclosed and verified without a duplicate request. Verify every
  affected order is terminal in Robinhood; a cancellation request is not proof of cancellation.
- If a placement acknowledgement is ambiguous, reconcile its exact client reference against Robinhood
  and never retry it blindly.

### 9. Enable supervised real-order ticket controls — you do this deliberately

The attended supervised experiment does not require or claim an autonomous evidence certificate. It
does require the constrained supported route and a new confirmation for every reviewed order:

1. Open **Settings & Permissions** in normal GRANDE Alpha.
2. Click **Apply bounded pilot settings** to preview Regular market, Market order, GFD, and cash T+1.
   Nothing changes outside the dialog until you explicitly click Save; **Restore opened values** and
   Cancel are available before saving.
3. Check **Make bounded real-order session controls available**.
4. Type the displayed exact phrase and save.

If Save is disabled, read the in-dialog reason and correct the constrained route or permission state.
The autonomous evidence status remains a separate lock and is not bypassed by supervised mode.

### 10. Authorize one bounded same-day supervised session — review it every live day

During the supported regular-session entry window, select **Authorize & Start Live Session** and review
the exact Agentic account, TQQQ/SQQQ scope, strategy fingerprint, route, expiry, per-order limit, daily
gross-notional limit, total exposure, daily loss, order count/rate, spread, and quote-age caps. Complete
the same-day typed confirmation yourself.

The grant is never stored or scheduled. Restart, expiry, revocation, account change, fingerprint change,
or unresolved broker state returns the app to locked. Each strategy ticket shows the immutable broker
preview and requires a fresh one-use typed confirmation before placement. Keep Robinhood open, monitor
receipts, and use **STOP + CANCEL** if state is unclear; inspect and explicitly confirm its exact
GRANDE-owned order preview before any cancellation request is sent.

The separate autonomous path remains unavailable until exact evidence, sealed holdout, and runtime
parity all pass for the same candidate. Supervised outcomes do not satisfy those gates, and neither
path is represented as profitable.

## What Codex or GRANDE Alpha cannot do for you

- manufacture qualifying market history or future forward-shadow time;
- turn a negative or unstable strategy into a truthful profitable one by changing thresholds;
- accept Robinhood consent, make inventory decisions, or authorize use of your money;
- provide legal, tax, employment, residency, account-eligibility, or jurisdictional clearance;
- obtain Robinhood's written public-product authorization on your behalf; or
- guarantee profit, prevent market losses, or make an evidence failure safe to bypass.

Until the checklist passes honestly, the correct autonomous action remains CASH or shadow-only.
