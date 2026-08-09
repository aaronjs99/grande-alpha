# Strategy and profit mechanics

## How this app can make money

There is only one intended source of trading profit: correctly identifying a short intraday QQQ
direction often enough that the leveraged ETF's favorable moves exceed unfavorable moves, spreads,
fees, slippage, and execution errors.

```text
QQQ one-minute prices
        |
  trend + momentum
        |
  bullish / bearish / flat
        |
 TQQQ / SQQQ / cash
        |
after-cost wins minus after-cost losses
```

The app does not create money through order frequency, artificial intelligence, the RTX 5070, or
holding TQQQ and SQQQ simultaneously. Frequent orders can make performance worse.

## Exact baseline rules

The implementation in `src/grande_alpha/strategy.py` uses completed QQQ midpoint bars:

| Parameter | Value |
|---|---:|
| Bar length | 1 minute |
| Warm-up | 24 completed bars |
| Fast EMA | 8 bars |
| Slow EMA | 21 bars |
| Minimum EMA separation | 4 basis points |
| Momentum lookback | 3 bars |
| Hard position stop | −0.8% from reported average price |
| Take-profit | +1.5% from reported average price |
| No new trades after open | First 5 minutes |
| No new trades before close | Last 10 minutes |

Decision rules:

- fast EMA above slow EMA by at least four basis points and positive three-minute momentum:
  **BULLISH**, target TQQQ;
- fast EMA below slow EMA by at least four basis points and negative three-minute momentum:
  **BEARISH**, target SQQQ;
- otherwise: **FLAT**, target cash.

The app never intentionally holds both ETFs. If both are detected, automatic trading locks. If the
regime changes, it sells the held ETF first. A later refresh may enter the new direction only if
buying power remains.

TQQQ and SQQQ seek approximately +3× and −3× the Nasdaq-100's **daily** return. Compounding means
multi-day performance can diverge substantially from simply multiplying QQQ's cumulative move.
Review the official [TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) and
[SQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/sqqq) disclosures.

## The $50-account math

At a $25 position size:

| Event | Gross percentage | Approximate gross dollars |
|---|---:|---:|
| Take-profit | +1.5% | +$0.375 |
| Hard stop | −0.8% | −$0.200 |
| 20-bps round-trip friction | −0.2% | −$0.050 |

The gross reward-to-risk ratio implied by those thresholds is 1.875 to 1. Ignoring costs and all
execution differences, the mathematical break-even win rate would be:

```text
0.8 / (1.5 + 0.8) = 34.8%
```

That does **not** mean a 35% observed win rate is profitable. Actual exits can occur before or after
the thresholds, market orders can slip, spreads vary, an app stop is not a broker-native bracket,
and cash settlement can prevent the next entry. Use actual filled prices and fees:

```text
expectancy per trade
= win_rate * average_after_cost_win
- loss_rate * average_after_cost_loss
```

Positive expectancy must be demonstrated by the account's realized fills; it cannot be assumed
from the source code.

## What tends to help

- A persistent directional QQQ move with liquid, narrow TQQQ/SQQQ spreads.
- Waiting through the opening noise instead of trading immediately at 9:30 Eastern.
- Remaining in cash when the EMA separation and momentum disagree.
- Small enough size that one loss does not change behavior.
- Stopping when data, account state, or execution cannot be verified.

## What tends to hurt

- Sideways, whipsawing QQQ conditions.
- Chasing after the move has already happened.
- Increasing order size after a loss.
- Manually trading the same symbols while automation is active.
- Running on stale quotes or wide spreads.
- Treating SQQQ as a perfect long-term hedge for TQQQ.
- Holding a local-software-managed position while the app, PC, internet, or Robinhood is down.

## Evidence required before increasing size

Do not scale because of one profitable day. Require at least 20 live sessions and record:

- number of submitted, filled, canceled, and rejected orders;
- actual entry and exit prices;
- gross and after-fee P/L;
- average spread at entry and exit;
- slippage versus the Robinhood review;
- win rate, average win, average loss, and expectancy;
- maximum session and peak-to-trough drawdown;
- number of risk locks, stale-data events, and broker/app mismatches.

Only consider increasing one limit at a time if after-cost expectancy is positive, drawdown is
acceptable, and no unresolved control failure occurred. Reduce size or stop if those conditions fail.

## What “success” means for the first Monday

Success is not a target dollar profit. It is:

- Robinhood and app data matched;
- one bounded session was authorized correctly;
- every signal and order matched the documented rules;
- no limit or warning was bypassed;
- the session ended with no unknown order or position;
- realized results were recorded honestly.

A profitable session with an unexplained order is a failed system test. A small losing session in
which every control behaved correctly is useful evidence, but it still does not prove an edge.
