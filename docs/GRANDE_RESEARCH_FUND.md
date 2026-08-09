# GRANDE Research Fund ledger

The Research Fund tab is a personal planning and evidence ledger. It never moves money, initiates
a bank transfer, deposits into a brokerage, or touches a university account.

## Calculation

```text
distributable = max(0, realized profit - fees - entered tax reserve)
eligible contribution = distributable × contribution rate
```

Only realized profit belongs in the first field. The estimate is not tax advice and the app does
not calculate the legally correct tax reserve.

## Procedure

1. Reconcile realized profit and fees against the broker's records for the selected month.
2. Enter a tax reserve chosen with appropriate tax advice and select a contribution percentage.
3. Save the entry. It remains `planned`; no money moves.
4. Separately transfer personal funds using the destination institution's normal controls.
5. Select the planned row, choose **Mark selected confirmed**, enter the independent transaction
   reference, and type the displayed amount-specific confirmation phrase.
6. Retain the broker statement, transfer receipt, and any tax calculation outside the app.

## Funds that must not be recorded as eligible profit

- University, lab, grant, sponsor, reimbursement, equipment, or robot funds.
- Borrowed money, credit-card advances, margin, or unsettled deposits.
- Unrealized gains, brokerage deposits, or returned principal.
- Rent, tuition, emergency reserves, tax reserves, or other essential liquidity.

The ledger database is `%LOCALAPPDATA%\GRANDEAlpha\grande_alpha.db`. On first launch, the app copies
recognized Momentum Trader configuration, database, and log files into the new directory when a
new counterpart does not already exist. The legacy originals are retained for recovery.
