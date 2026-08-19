# Capital planning ledger

The optional Capital Planning Ledger is a generic planning and evidence feature. It never moves
money, initiates a bank transfer, or deposits into a brokerage account. It is disabled by default.

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
4. If appropriate, separately complete the transfer using the source and destination institutions'
   normal controls.
5. Select the planned row, choose **Mark selected confirmed**, enter the independent transaction
   reference, and type the displayed amount-specific confirmation phrase.
6. Retain the broker statement, transfer receipt, and any tax calculation outside the app.

## Amounts that must not be recorded as eligible profit

- Money the operator does not own or is not authorized to allocate.
- Borrowed money, credit-card advances, margin, or unsettled deposits.
- Unrealized gains, brokerage deposits, or returned principal.
- Essential liquidity, tax reserves, or other protected amounts.

The ledger database is `%LOCALAPPDATA%\GRANDEAlpha\grande_alpha.db`. On first launch, the app copies
recognized Momentum Trader configuration, database, and log files into the new directory when a
new counterpart does not already exist. The legacy originals are retained for recovery.
