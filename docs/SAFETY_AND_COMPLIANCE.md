# Safety, account, F-1, and tax boundaries

This document identifies operating boundaries; it is not individualized legal or tax advice.

The **SANDBOX** tab is non-financial simulation: it uses fictional aliases and cannot contact the
broker. The rest of the application remains capable of real-money trading after explicit live
authorization; a successful sandbox run never grants or implies that authority.

**Live shadow** reads current quotes but still makes only fictional fills. It is visibly labeled,
one-click revocable, receipt-producing, and mutually exclusive with real-order authority. Neither a
sandbox gate nor a shadow result can silently broaden consent into live trading.

## Robinhood account boundary

The app selects an active account that Robinhood reports as `agentic_allowed=true`. It can read
other connected-account data exposed by Robinhood but submits orders only to that Agentic account.
The current account has been identified as a cash individual account ending in 8900.

Robinhood describes Agentic Trading as capable of placing orders without per-transaction input when
the user has asked for automation, and warns that AI-driven trading can lose the entire investment
or be difficult to monitor and stop. See Robinhood's
[Agentic Trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
and [trading-tool documentation](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).

The app narrows that authority:

- TQQQ and SQQQ buys only; sells reduce those positions;
- one time-limited session;
- numeric order, exposure, loss, rate, spread, and staleness caps;
- every Robinhood review warning blocks and locks automation;
- immediate STOP + CANCEL control;
- receipts for each consequential action;
- OAuth credentials never pass through the app.

## Cash-account rules still matter

Robinhood states that its pattern-day-trader designation and $25,000 minimum ended for Robinhood
margin accounts on June 4, 2026. That does not turn a cash Agentic account into a margin account.
Cash-account settlement, good-faith, and freeriding rules still apply. Securities generally settle
on T+1. See [Robinhood's current PDT page](https://robinhood.com/us/en/support/articles/pattern-day-trading/),
the [SEC T+1 FAQ](https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq), and
[FINRA brokerage-account guidance](https://www.finra.org/investors/investing/investment-accounts/brokerage-accounts).

Consequences for this app:

- a sale does not guarantee immediately reusable buying power;
- a same-day direction flip may exit successfully but be unable to fund the new entry;
- the app trusts broker-reported buying power and stops rather than assuming proceeds are usable;
- do not bypass a broker settlement warning manually.

## F-1 boundary

The IRS generally states that a nonresident alien's own-account securities trading through a U.S.
broker does not itself constitute a U.S. trade or business. That is a tax classification, not a DHS
immigration safe harbor. DHS directs F-1 students to work with their designated school official on
employment questions. See [IRS Publication 519](https://www.irs.gov/pub/irs-pdf/p519.pdf) and
[DHS Study in the States](https://studyinthestates.dhs.gov/students/resources/working).

Before live use, obtain a written answer from the DSO and, if needed, an F-1 immigration attorney
who understands active automated trading. Do not use this app to:

- manage anyone else's money;
- accept outside capital;
- sell signals or copy-trading access;
- charge subscription, management, or performance fees;
- commercialize trading services;
- describe the activity as employment or a trading business without professional guidance.

The live-session checkbox is an attestation, not legal clearance. If it is not true, do not check it.

## Tax records

The user previously reported two unfiled tax years. Live automation can create many additional tax
lots and wash-sale interactions, so establish resident/nonresident status and catch up filings with
a professional familiar with F-1 taxpayers before scaling activity.

Retain:

- W-2, 1042-S, 1099, and other income forms;
- Robinhood consolidated 1099 and transaction exports;
- every executed fill, fee, transfer, and tax lot;
- the app's `%LOCALAPPDATA%\GRANDEAlpha\grande_alpha.db` audit database;
- the daily journals;
- records of substantially identical purchases in other accounts.

Robinhood fills and consolidated tax documents—not the app's estimated P/L—are authoritative for
filing. Review [IRS Topic 429](https://www.irs.gov/taxtopics/tc429) and
[IRS Publication 550](https://www.irs.gov/publications/p550). Do not assume a refund or a balance
due until residency, withholding, income, gains, losses, treaty treatment, and filing status are
verified.

## Local-stop limitation

Robinhood does not provide native stock bracket/OCO orders through the supported order types used
here. The −0.8% stop and +1.5% target are app decisions. They cannot execute while the app, PC,
internet, OAuth session, or Robinhood service is unavailable. Keep position size small enough that
this limitation is acceptable, and keep the Robinhood app available for manual intervention.
