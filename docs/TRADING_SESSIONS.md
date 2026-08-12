# Trading sessions and automatic order routes

GRANDE Alpha exposes the three equity sessions currently accepted by Robinhood Trading MCP. A
selection is an execution constraint, not a prediction or a reason to trade.

| User selection | Eastern time | Automatic order choices | Sizing |
|---|---|---|---|
| Regular market | 9:30 AM-4:00 PM | Market GFD; limit GFD/GTC | Market buys may use dollars/fractions; limits use whole shares |
| Extended market | 7:00 AM-8:00 PM | Limit GFD/GTC only | Whole shares |
| 24 Hour Market | 8:00 PM-8:00 PM on eligible trading days | Limit GFD/GTC only | Whole shares; live eligibility rechecked before submission |

Robinhood does not execute equity market orders during extended or overnight sessions. A market
order sent then may queue for regular open, so GRANDE Alpha does not create that combination. The
provider can also change symbol/session eligibility. Its review and placement responses remain
authoritative. See Robinhood's current [extended-hours](https://robinhood.com/us/en/support/articles/extendedhours-trading/),
[24 Hour Market](https://robinhood.com/us/en/support/articles/24hour-market/), and
[Agentic Trading](https://robinhood.com/us/en/support/articles/agentic-trading-overview/) disclosures.

## Where the choice is made

**Settings & Permissions → Automatic order route defaults** stores a default only. It grants no
authority. **Safety → Authorize Live Session** displays the route again and allows the user to change
it before typing the account-specific confirmation. The resulting live grant binds the exact session,
order type, time in force, limit offset, account, ticker tuple, and strategy fingerprint. It expires
within the same Eastern calendar day and is never restored after restart. Any intent that differs is
rejected locally before broker review.

The sandbox exposes the same fields. Evidence-policy version 13 binds them and the settlement model
into the strategy fingerprint, adds a complete-session-coverage gate, and requires a one-use final
holdout. Regular data cannot certify an extended or
overnight route. The community adapter can request pre/post-market bars for extended research, but it
rejects 24-hour certification because it does not provide complete overnight coverage. Use a lawful,
aligned QQQ/TQQQ/SQQQ CSV with overnight timestamps and a consistent
`market_hours=all_day_hours` column for that research path. The importer requires both evening and
overnight observations and checks missing intervals across the 8:00 PM trading-date boundary.
The dataset interval must also match the live analysis interval. The sandbox includes a custom
1-300 second CSV choice; set it to 5 seconds for the default live cadence. A 1-minute run cannot
certify a 5-second strategy.

## Marketable-limit construction

For an authorized offset of `b` basis points and current quote `(bid, ask)`, the live controller uses:

```text
buy limit  = ceil_to_cent(ask × (1 + b / 10,000))
buy shares = floor(authorized notional / buy limit)

sell limit  = floor_to_cent(bid × (1 - b / 10,000))
sell shares = complete whole-share position quantity
```

The offset is a maximum price concession, not expected slippage. A limit can partially fill or never
fill. If the authorized notional cannot buy one whole share, the controller records a blocked decision
instead of exceeding the budget. If a selected limit route encounters fractional inventory, automatic
trading locks rather than leaving an opposite leveraged position on top of a fractional remainder.
The separately reviewed manual flatten remains a regular-hours market order so fractional inventory
can be represented; outside regular hours it may queue at Robinhood.

## GFD versus GTC

- GFD expires at the end of the selected provider trading day/session.
- GTC can remain working at Robinhood for up to 90 calendar days.

GRANDE Alpha detects a pending broker order and will not submit another one. Only the explicit
**STOP + CANCEL** flow can request cancellation: it previews the exact GRANDE-owned nonterminal
Agentic orders, requires confirmation, excludes manual/unrelated orders, and verifies an already
pending cancellation without sending it twice. Disconnect and orderly shutdown never cancel; they
lock and refuse while owned open or unresolved state remains. A GTC order can still survive an
application, network, operating-system, or power failure and may fill without the app running. Choose
GTC only if that persistence is intentional and monitor the authoritative Robinhood order view.

The local clock handles weekdays and selected session boundaries. It is not an exchange calendar;
holidays, halts, venue outages, liquidity, account restrictions, and final eligibility are enforced by
Robinhood review/placement and can still prevent or delay execution.
