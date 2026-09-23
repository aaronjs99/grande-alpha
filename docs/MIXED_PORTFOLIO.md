# Mixed earnings and leveraged-ETF portfolio

Status: broker-isolated allocation research, not an enabled live strategy. This module does not
relax the existing live TQQQ/SQQQ route, grant multi-day authority, or submit individual-stock orders.

## Implemented research logic

- Earnings candidates pass the existing point-in-time EPS/price/quote screen. Invalid rows stop
  planning; repeated events for one symbol require deliberate event selection.
- Stock scores combine bounded surprise and post-announcement momentum strength, scaled down for
  volatility. They are heuristics, not return forecasts, confidence probabilities or proven alpha.
- The ETF sleeve selects TQQQ in a qualified bullish regime, SQQQ in a qualified bearish regime,
  and neither in neutral, stale, high-spread, excessive-volatility or low-confidence conditions.
  Regime/confidence inputs must be supplied; the planner does not claim to have estimated them.
- Relative stock/ETF scores change the proposed split. Cash retains unused capacity rather than
  automatically filling every cap. There is no requirement to invest the full account.
- Individual-stock, stock-sleeve, ETF-sleeve, sector and gross-exposure caps apply. The gross proxy
  counts stock dollars once and leveraged-ETF dollars three times, without inverse-hedge credit.
  This is not a covariance model, Nasdaq beta estimate, VaR or maximum-loss guarantee. ETF sector
  holdings are not looked through; the sector cap covers the stock sleeve only.
- Holdings carry their original open time across dates. Thesis failure or a configurable holding
  horizon produces an exit review even when the position is losing. The engine does not wait for
  profitability or imply that a review was executed. Position age and session authority are separate.

The template contains configurable **research starting points**, not optimized or activated settings:
80% maximum invested, 60% stock sleeve, 20% ETF sleeve, 20% per stock, 40% per stock sector, and a
1.0 gross-leverage proxy cap. Holding-review horizons start at 20 calendar days for stocks and two
for leveraged ETFs. The user may change these in research; live adoption requires qualification.
No personal account or capital amount is embedded in public defaults.

ProShares describes TQQQ as a daily-target fund. Returns over longer periods can differ from its
daily multiple; multi-day eligibility is not permission to ignore compounding and volatility risk.
See [the issuer's product explanation](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq).

## Offline use

```powershell
.\cli.ps1 portfolio template
.\cli.ps1 portfolio plan --input C:\private\mixed-research.json
```

The request includes `capital_usd` (total modeled portfolio value, including cash), all policy fields,
an [earnings screen request](EARNINGS_RESEARCH.md), timestamped market-regime data, per-stock sector/
volatility metadata, and current long-only holdings. `daily_volatility` is a fractional return
standard deviation, not a percent number. Each holding has exactly `symbol`, `market_value`,
`opened_at`, and boolean `thesis_valid`. Holdings cannot exceed the supplied modeled account value.

Targets and rebalance reviews are not orders. Smaller changes are marked below a configurable review
band; the target allocation itself is not a promise that those trades are executable. Sales and
unsettled proceeds cannot be assumed to finance buys. Input and policy hashes support reproducibility;
input provenance remains user-supplied and unverified.

## Still required for the requested live product

1. A point-in-time earnings/consensus source and licensed quote/risk data with delistings and corporate
   actions; no credentials or paid subscriptions have been installed.
2. The [multi-day paper replay](PORTFOLIO_REPLAY.md) now models later-quote execution, costs, settlement
   and overnight gap stops. Qualification still needs real data, partial-fill/volume/corporate-action
   models, out-of-sample comparison and parameter sensitivity.
3. [Stock-capable tickets, risk preflight and an isolated execution ledger](EQUITY_EXECUTION.md) are
   implemented and offline-tested. Dedicated strategy dispatch and provider eligibility integration
   remain unfinished. The current live route remains leveraged-ETF-only.
4. Multi-day operating authority and recovery across market dates, separate from permission to hold
   overnight. Existing bounded authority still expires and requires a new deliberate grant.
5. Simulated and provider-observed end-to-end qualification of the combined strategy and exits.

## Device-only notifications

New warning/error/critical receipts create a persistent notification in the same local audit
database. The live CLI also displays new notices in its existing terminal, with control characters
escaped. No email, webhook, external delivery service or pop-up process is used.

```powershell
.\cli.ps1 notifications --unread
.\cli.ps1 notifications --ack 12
.\cli.ps1 notifications --after 12
```

Acknowledgement marks one notification read; it does not erase its receipt, resume trading or
approve orders. The inbox survives restart. These are terminal/inbox notifications, **not Windows
toast notifications**. If the PC is off, frozen or disconnected, it cannot notify someone elsewhere.
GUI inbox integration and native toast delivery have not been implemented or tested.
