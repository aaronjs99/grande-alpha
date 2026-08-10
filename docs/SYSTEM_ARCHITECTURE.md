# GRANDE Alpha system architecture

GRANDE Alpha is a sibling project in the GRANDE family. It borrows GRANDE's gated-autonomy and
evidence patterns, but it is not part of the robot runtime and does not imply marine or field
readiness.

```text
GRANDE project family
├── GRANDE Marine       robotics, sensing, navigation, and field validation
├── GRANDE Research     experiments, papers, and evidence governance
└── GRANDE Alpha        personal trading workstation and contribution ledger
```

## Reused design pattern

| GRANDE concept | GRANDE Alpha implementation |
|---|---|
| Planner proposes an action | Deterministic strategy proposes a trade intent |
| Independent runtime bounds | Risk engine independently approves or rejects the intent |
| Freshness and lifecycle gates | Quote age, spread, market-time, and session-state checks |
| Actuator boundary | Official Robinhood Trading MCP review and order submission |
| Emergency stop | `STOP + CANCEL` blocks new orders and requests cancellation |
| Evidence trail | SQLite receipts record decisions and broker responses |
| Candidate versus approved runtime | `LOCKED`, `LIVE`, `EXPIRED`, and review-blocked states |
| Simulation boundary | `TQQQS`/`SQQQS` replay engine receives no broker object or live authority |
| Candidate observation | Live shadow consumes current quotes but has no broker-order dependency |
| Low-latency observation | Single-flight quote loop is independent of slower account reconciliation |

## Hard separation

- No ROS dependency, shared process, database, credential, or runtime authority.
- No university, lab, grant, reimbursement, equipment, or robot funds may enter the brokerage
  account or the Research Fund ledger.
- No nonpublic sponsor, procurement, or research information may become a trading signal.
- Trading profit is not evidence that a robotics algorithm works.
- A simulation, backtest, or successful trade is not evidence of marine field readiness.

The permitted connection is organizational and evidentiary: GRANDE Alpha uses the same style of
bounded authority, explicit confirmation, stop control, and auditable receipts. Its Research Fund
feature records only intended and externally confirmed contributions of personal realized profit.

The sandbox and live-shadow executor share a pure decision policy with live automation. The policy
returns a target and reason; three separate execution boundaries consume that decision. Historical
replay writes virtual accounting tables, live shadow records virtual receipts from current quotes,
and only the live controller may request an official Robinhood review and order. Shadow and live
authority are mutually exclusive.

The runtime uses four clocks. A batched quote request targets the configured fast cadence and drops
overlapping timer ticks. Completed QQQ bars update analysis, while a slower integer bar stride selects
one exact `(T,S)` pair action, enforcing `t_analysis < t_trade`. Portfolio, position, and order truth
is reconciled separately. Broker review, open-order detection, a 12-second submission cooldown, and
the session's orders-per-minute limit remain independent gates. Two-leg rotations sell first and wait
for fill/reconciliation before buying; commands are not treated as atomic. This is designed to remain
stable when remote latency exceeds the local timer; it is not an exchange feed or colocated execution engine.

The research sandbox selects a finite strategy through a versioned factory. Each strategy accepts
completed QQQ bars and returns the same bullish, bearish, or flat signal contract. The factory does
not provide broker access. Live automation remains the explicit EMA baseline; a research strategy's
different fingerprint cannot authorize that live path.
