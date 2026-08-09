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

The sandbox is a third execution boundary inside GRANDE Alpha. It shares strategy mathematics and
the audit store, but it has its own virtual accounting tables and no dependency on the broker,
OAuth, account discovery, order review, order placement, or cancellation code.
