# Strategy and profit mechanics

## Runtime champion: CASH / hold

Normal and scheduled runtime default to the deterministic **CASH / hold** strategy. It always emits
a flat regime, maps to the pair action `(0,0)` when no position is held, and requests no TQQQ or
SQQQ entry. This is the current evidence-backed fail-safe choice: the available intraday benchmark
did not demonstrate positive after-cost out-of-sample performance. Cash is not a profit guarantee;
it is the decision not to take modeled leveraged exposure.

Other supported policies remain available as deliberate shadow/research selections. Selecting one
changes the evidence fingerprint and resets the runtime signal pipeline. It does not establish an
edge or unlock live authority.

## How this app can make money

There is only one intended source of trading profit: correctly identifying a short intraday QQQ
direction often enough that the leveraged ETF's favorable moves exceed unfavorable moves, spreads,
fees, slippage, and execution errors.

```text
completed QQQ prices
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

## Finite research library

The application deliberately does not claim to contain “all strategies ever.” Its sandbox has a
small, auditable library whose hypotheses can be falsified on the same data and cost assumptions:

| Candidate | QQQ signal hypothesis | Important limitation |
|---|---|---|
| EMA momentum | Fast trend plus recent momentum agreement | Whipsaws in ranges |
| Multi-horizon trend | Short, medium, and longer returns agree | Academic evidence is generally at slower horizons |
| Closing momentum | Rest-of-day direction persists in the final half-hour | Time-specific and sensitive to close execution |
| Opening breakout | Price clears the completed opening range | False breaks and opening spreads |
| Conservative ensemble | Multiple causal sleeves agree | Agreement can reduce trades without creating edge |

Every candidate uses only completed bars. The library excludes GPU/RL optimization and any VWAP,
volume, or spread-dependent alpha until those exact live inputs exist in replay and production.
Research candidates cannot unlock a selected runtime path merely by doing well: fingerprints differ.

## Deliberate EMA research-runtime rules

When `EMA momentum` is deliberately selected instead of the cash champion, the implementation in
`src/grande_alpha/strategy.py` uses completed QQQ midpoint bars:

| Parameter | Value |
|---|---:|
| Completed analysis bar | 5 seconds |
| Pair-action decision | Every 3 analysis bars (15 seconds nominal) |
| Warm-up | 24 completed bars |
| Fast EMA | 8 bars |
| Slow EMA | 21 bars |
| Minimum EMA separation | 4 basis points |
| Momentum lookback | 3 bars |
| Hard position stop | −0.8% from reported average price |
| Take-profit | +1.5% from reported average price |
| No new trades after open | First 5 minutes |
| No new trades before close | Last 10 minutes |

In live and live-shadow operation, the application builds those 5-second bars locally from the
quote midpoints it observes. The current remote-history adapter's finest interval is 1 minute; it
does not provide native 5-second history. Therefore, 1-minute replay results cannot validate the
5-second baseline, and a 5-second CSV is relevant only if its provenance shows genuinely observed
or source-provided 5-second bars. Resampling a 1-minute candle does not recover the missing path.

The final ten minutes are an exit window: new entries are blocked, the policy targets cash, and
risk-reducing sells remain permitted through the 4:00 p.m. Eastern regular-session close.

Decision rules:

- fast EMA above slow EMA by at least four basis points and positive three-bar momentum:
  **BULLISH**, target TQQQ;
- fast EMA below slow EMA by at least four basis points and negative three-bar momentum:
  **BEARISH**, target SQQQ;
- otherwise: **FLAT**, target cash.

The app never intentionally holds both ETFs. If both are detected, automatic trading locks. If the
regime changes, it sells the held ETF first. A later refresh may enter the new direction only if
buying power remains.

TQQQ and SQQQ seek approximately +3× and −3× the Nasdaq-100's **daily** return. Compounding means
multi-day performance can diverge substantially from simply multiplying QQQ's cumulative move.
Review the official [TQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq) and
[SQQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/sqqq) disclosures.

## Position-size math

For a hypothetical position notional `N`:

| Event | Gross percentage | Gross result before other costs |
|---|---:|---:|
| Take-profit | +1.5% | `+0.015 × N` |
| Hard stop | −0.8% | `−0.008 × N` |
| 20-bps round-trip friction | −0.2% | `−0.002 × N` |

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

## What `cash_t1` changes for a $50 cash ledger

The default research and shadow settlement model permits buys only from settled cash. When a
virtual position is sold, net proceeds move to unsettled cash. They remain part of total equity but
are unavailable for another entry until the next observed market session, when the model releases
them back to settled cash.

If one $50 tranche is used for a round trip, that tranche is normally unavailable for another entry
that session. Smaller tranches permit more independent entries only until each settled tranche has
been used. The `instant` option is a research counterfactual, not evidence that a broker will permit
same-session reuse. Real buying power, settlement timing, restrictions, and order acceptance come
only from the broker.

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

Do not scale because of one profitable day. Shadow-only operation remains the required engineering
stage while there is no passing policy-v11 certificate. If a later qualified review permits real
orders, require at least 20 monitored live sessions and record:

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

## What “success” means for a first evaluation

For the next shadow-only engineering session, success is not a target dollar profit. It is:

- Robinhood and app data matched;
- no real-order session was authorized and no real order was submitted;
- every signal and virtual fill matched the documented rules;
- no limit or warning was bypassed;
- settled and unsettled cash moved according to `cash_t1`;
- the session ended with no unknown broker order or position;
- virtual results and data gaps were recorded honestly.

A profitable shadow session with an unexplained transition is a failed system test. A small losing
session in which every control behaved correctly is useful engineering evidence, but it still does
not prove an edge or guarantee future profit.
