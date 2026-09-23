# Bounded unattended engine

Status: source implementation with offline simulated-broker tests. Not activated, deployed,
strategy-qualified, or verified with real orders. An awake Windows PC can host this foreground
runner; no cloud subscription, GUI, scheduled task, auto-start, or sleep-setting change is required.

## Authority and broker compatibility

`engine run-unattended` is separate from the attended `engine run-live` route. It requires current
exact strategy evidence, every grant limit, explicit standing terms, fresh account/quote preflight,
and a typed session approval in an interactive terminal. Supervised experimental exemptions cannot
be used for unattended operation. The empty template grants nothing.

The session approves the exact candidate's sizing within the supplied grant limits, including
strategy sells and loss exits. It expressly skips broker review and per-ticket confirmation.
It does not pretend that a person approved each ticket or that a local quote check is a broker review.

Robinhood's placement metadata observed on 2026-09-07 permits explicit review bypass; its cancellation
metadata still requires user confirmation. The engine pins the full observed tool-inventory digest
and official server URL. An unfamiliar connection contract fails closed and requires engineering
review; editing a policy cannot approve a new provider contract. Metadata is checked from the
connected adapter's discovery snapshot, not continuously rediscovered from the server.

Automatic cancellation is deliberately unsupported. Stopping cannot undo a request already sent.
This is not a guarantee of exact-once broker execution or a guarantee that a loss threshold will
cap realized losses. Broker outages, price gaps, partial fills and unclosed positions remain risks.

## Prepare and validate; do not activate an unqualified candidate

```powershell
.\cli.ps1 engine standing-template
.\cli.ps1 engine policy-check --unattended --policy C:\private\standing-policy.json
```

The standing policy extends the attended policy with this explicit `standing` object:

```json
{
  "skip_broker_review_and_per_order_confirmation": true,
  "sizing": "exact_candidate_within_grant_limits",
  "allow_strategy_sells_and_loss_exits": true,
  "automatic_cancellation": false,
  "restart": "recover_same_unexpired_unchanged_authorization",
  "stop": "revoke_new_orders_residual_exposure_may_remain"
}
```

All other fields follow [the session policy schema](LIVE_CLI.md). No account, money amount, strategy,
or duration is chosen automatically. After strategy qualification and operator approval, activation is:

```powershell
.\cli.ps1 engine run-unattended --policy C:\private\standing-policy.json --connect
```

Local OAuth and the typed `SKIP REVIEW AND ARM` phrase must be completed by the operator. Session
authority expires within the same Eastern trading day, at most 360 minutes. Staying awake does not
renew it. This release does not promise indefinite, overnight or multi-day autonomous operation.

## Stop and recovery

In a second terminal on the same PC and Windows user/data directory:

```powershell
.\cli.ps1 engine stop
```

This records revocation for all local standing sessions without taking the runner's instance lock
or contacting the broker. The runner checks revocation before placement and polls it approximately
every 100 ms while awaiting broker responses. This is not a hard real-time bound: a frozen process,
OS or storage device can delay observation. Ctrl+C also stops the foreground session. Neither
control requests cancellation or liquidation. Verify open orders and exposure in Robinhood.

Each logical order is durably recorded with its original reference before placement. Counts and
loss-stop history survive a restart. Ambiguous placement outcomes are not retried. The mixed engine
can recover only the same unexpired, unchanged durable authority after obtaining the exact account
lease; stopped, changed and expired authorities remain unusable. Missing or unreadable stop/risk
state fails closed. Keep the audit and execution databases intact.

The runner never resumes itself after a crash. There is no installed scheduler. Delivery of alerts,
native-host outage testing, and provider-observed partial/fill/cancellation recovery qualification
remain separate work. Offline tests inject fabricated evidence readiness solely to exercise engine
mechanics; production evidence gates are unchanged.
See [mixed production qualification](PRODUCTION_QUALIFICATION.md) for the newer stock/ETF path.
The execution change advances the evidence policy to version 15; older evidence cannot silently
qualify this new order path.
