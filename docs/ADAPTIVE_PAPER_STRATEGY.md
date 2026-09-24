# Adaptive trend paper experiment

New desktop sessions select **Adaptive trend · paper experiment** under **Session
setup → Paper strategy**. The dashboard, agent cards, charts, activity log and themes
retain their layout. **Legacy · rules or AI decisions** remains available for comparison.
Stop and start a new virtual session to change strategies. Research-only runs and the
synthetic demo retain their existing behavior. No broker orders are enabled.

The previous configuration could accept an AI reply yet remain unable to enter:
the default ETFs often had no two-publisher coverage, and decisions waited on local
inference. Adaptive mode separates price decisions from optional AI context. It is
a separately selected and recorded policy, `adaptive-trend-v1`, not an automatic
fallback from the legacy AI strategy after an error.

## Entries

After four distinct quotes spanning 60 seconds, retain up to ten minutes of valid
quotes. Require the 30-second time-weighted exponential trend above the 120-second
trend, the current midpoint above the fast trend, and a breakout above the preceding
30 seconds. The observed move must exceed the larger of 1.5 times the current modeled
round-trip cost and twice observed return noise. Costs include the actual bid/ask
spread and adverse slippage on both sides. Noise is the population standard deviation
of consecutive midpoint returns, multiplied by the square root of the return count.
These are experimental heuristics, not forecasts of profit. Costs consuming the 1%
stop budget block entry. A pending entry must still qualify on its later fill quote.

| Control | Adaptive paper behavior |
| --- | --- |
| News enabled | Read headlines and block new entries on configured risk terms. Missing coverage does not itself block a price signal. |
| Optional local AI | At most one request per market; starts at least 60 seconds apart. Rechecked replies are advisory context and cannot force or veto trades. Pending/failed replies do not suspend price decisions. |
| Capacity | Defaults to four held or pending entries across stocks and crypto and at most 40% of starting virtual cash committed at entry cost. Saved user controls can reduce these caps to 1–4 positions and 5–40% exposure. |
| User pause | Saved pause blocks new virtual buys and cancels pending virtual buys; holdings keep their normal exits. Available through Pause new buys on the dashboard or Paper controls in AI chat. |
| ETF overlap | Hold or queue only one of QQQ, TQQQ and SQQQ at a time. |
| Stop / target | Request an exit at -1% or +2% estimated net liquidation return after sell slippage. |
| Other exits | 0.75% decline from the observed bid high, downward trend reversal, or 30 minutes in the position. |
| Re-entry | Wait 60 seconds after a virtual sale. |
| Drawdown | At 3% maximum observed session drawdown, stop new entries for that session; continue managing holdings. |

Exits evaluate eligible quotes without waiting for AI or rebuilding entry warm-up.
Fills still require a later eligible quote. Gaps, closed sessions, stale/wide quotes
and pair restrictions can delay exits and cause losses larger than the stop level.
These are virtual instructions, not broker-held stops. The roughly 2% crypto spreads
in the reported session still exceed the existing 1% limit. This strategy does not
fabricate cheaper quotes or bypass crypto restrictions.

The original two-publisher filter and model-controlled decisions still apply in
**Legacy** mode. Policy and AI/news roles are recorded in the paper ledger, diagnostics
and research MCP context. The AI receives at most 25 recent samples per instrument,
plus the price strategy's numeric metrics, to bound inference work.

## Validation and limits

Tests cover rising/reversing, falling, flat and noisy prices, AI delays/errors/HOLDs,
missing news, headline risks, entry limits, cooldowns, and exits. They include losing
trades after a gap; they verify behavior with synthetic inputs, not future returns.
Forward paper observations are needed to assess whether this policy improves on the
baseline. More trades are not evidence of a better strategy. The paper ledger still
omits fees, liquidity limits and settlement constraints, so its results can overstate
real execution performance. No strategy parameters were fitted to user trade outcomes.
