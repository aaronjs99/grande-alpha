# Attended live CLI

Status: implemented with offline tests; not validated by a real-money run. This is **not**
unattended operation, an earnings strategy, or a profitability claim. No scheduler is installed.

The CLI and desktop share the same controller, risk checks, audit database, and instance lock.
`engine run` remains structurally read-only shadow. `engine run-live` can place real orders only
after explicit session and exact-ticket approvals in an interactive terminal. Piped approval and
`--yes` are not supported. Never run another agent against the same account independently.

## Prepare an explicit policy

`grande-alpha-cli engine policy-template` prints a JSON skeleton. Save it privately outside the
repository and supply every field. Null values intentionally make the template unusable until
the user chooses an account, candidate, route, limits, and duration. Do not put account numbers or
credentials into version control. OAuth credentials do not belong in the policy.

The top-level fields are `authority_mode`, `session_minutes`, and `grant`.

- `evidence_gated`: requires current evidence for the exact installed strategy and requested limits.
- `supervised_experimental`: explicitly selects the existing small, attended experimental route.
  It does not manufacture evidence or certify an autonomous strategy. Existing controller caps apply:
  $10 per order, $50 gross daily notional, and $40 exposure.

These caps are product ceilings, not chosen user budgets. Both modes require fresh approval of every
order, including sells. There is no automatic fallback between modes. Sessions expire within the
same Eastern calendar day and must last no more than 360 minutes.

The `grant` fields correspond exactly to `LiveGrant`: account number; strategy SHA-256 fingerprint;
allowed symbols; market hours; order type; time in force; limit offset; per-order, gross-daily,
exposure, and daily-loss limits; trade and rate caps; maximum spread; maximum quote age.
Current execution scope remains TQQQ/SQQQ, regular-hours market/GFD, cash-T+1, zero modeled latency.
The per-order cap must not exceed either the exposure or daily gross-notional cap. Loss thresholds
are stopping rules, not guarantees: slippage, gaps, unavailable approvals, and existing exposure
can cause realized losses beyond a threshold.

Use `status --json` / `activation --json` for local strategy and gate inspection. A fingerprint
must correspond to the exact installed candidate and route; a changed candidate needs a new policy.

```powershell
grande-alpha-cli engine policy-check --policy C:\private\session.live-policy.json
grande-alpha-cli engine run-live --policy C:\private\session.live-policy.json --connect
```

The first command validates the policy offline and grants no authority. The second requires an
interactive terminal and initially connects through the read-only facade for account preflight.
`--authenticate` permits browser OAuth only when a person is available to complete it; cached
authentication is the default. The application window is not opened.

The session prompt displays the exact account, strategy, limits, route, expiry, and authority
digest. A session approval does not approve any ticket. Each ticket separately displays its
reviewed quote, estimated amount, checks, disclosures, and expiry. A typed response is bound to the
exact preview; rejection or expiration never becomes approval. Price changes may require a new
preview. Terminal escape characters in provider text are escaped rather than executed.

## Stop and failure behavior

The observed account-value loss stop is persisted per account and Eastern trading day. Restarting,
re-arming, or a later balance recovery cannot clear a recorded breach. The strictest loss threshold
selected that day remains in effect. A new day requires fresh authority; it does not restart trading.
If same-day placements exist but their loss history is missing, new authority is refused. Keep the
audit database intact; deleting it is not a supported reset. A failed risk-state write revokes authority.

This measures observed peak-to-current account value, not isolated strategy profit or loss. Cash
flows can affect it, and price moves during outages are unobserved. It does not guarantee a maximum
loss or implicitly approve liquidation. Risk semantics changed under evidence policy version 14;
older evidence must be regenerated and qualified, not relabeled.

Ctrl+C interrupts this foreground process. Read failures, request timeouts, authority revocation,
and session expiry stop the driver. It does not blindly retry failed placements. The existing
controller's durable intent/reconciliation rules continue to govern uncertain outcomes.

Stopping revokes local authority; **it does not cancel orders, liquidate positions, or prove that
an in-flight request failed**. Check Robinhood for the final account state. Cancellation requires
its own exact user confirmation under the observed provider contract. During an outage or an
unattended period, do not assume the program can close risk on your behalf.

## Cloud and strategy development boundary

The Robinhood adapter uses Streamable HTTP MCP directly; a CLI is one client interface, not a
provider requirement. [Robinhood's official guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
documents both desktop and CLI clients. Moving development to another chat does not deploy an
always-on runtime or transfer this machine's credentials or uncommitted files.

Post-earnings-announcement drift (PEAD) now has a separate [research-only candidate screen](EARNINGS_RESEARCH.md).
The live leveraged-ETF strategy does not implement it. A separate event dataset, point-in-time
expectations, actual release timestamps, tradable entry times, costs, and out-of-sample evaluation
are required before comparing it with existing strategies. The claim that it is the best marker
has not been tested here. Do not substitute ETF momentum for a company-earnings strategy.

An LLM proposal stage and unattended placement/cancellation authority are not implemented by this
command. They must not be represented as human approval callbacks or enabled by weakening evidence.

Use `engine readiness` and the [autonomy handoff](GETTING_TO_AUTONOMY.md) for consolidated next steps.
