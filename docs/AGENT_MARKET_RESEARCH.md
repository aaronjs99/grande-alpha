# News, social context and paper evaluation

The Agent workspace can collect public headlines and optional social posts, attach
their sources to analysis, and record subsequent virtual trade results. This is an
experimental research pipeline. It does not establish a profitable strategy, train
a model automatically, or enable live stock/crypto orders.

## Turn it on

1. Connect Robinhood for quotes. On **Agent · Stocks + Crypto**, open **Session setup**
   and choose **Robinhood quotes**.
2. Check **Read news and apply headline checks to new buys**. Optionally check
   **Include public social context · Bluesky, when available**. These options start off;
   no news-provider key is required. Stop the current run before changing settings.
3. Set your stock/crypto watchlists in **Configure universe and AI**, then click
   **Start continuous paper trading**. Cash and fills are virtual. Scroll to **News + trends**
   for feed status and articles; double-click a headline to open its publisher page.

The offline demo skips all external sources, even when these options are checked.
Its fixed price pattern is for exercising the interface, not evaluating a strategy.
Research-only runs can also use news through the same session settings.

## What is collected

| Source | Material | Address |
| --- | --- | --- |
| BBC Business | Business headlines and RSS excerpts | https://feeds.bbci.co.uk/news/business/rss.xml |
| CNBC | Top news headlines and RSS excerpts | https://www.cnbc.com/id/100003114/device/rss/rss.html |
| CoinDesk | Crypto headlines and RSS excerpts | https://www.coindesk.com/arc/outboundfeeds/rss |
| Federal Reserve | Official monetary-policy announcements | https://www.federalreserve.gov/feeds/press_monetary.xml |
| Bluesky, optional | Public English-language cashtag search, labeled unverified | https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts |

Feeds refresh at most once every ten minutes for the same settings, on the next
analysis cycle. Each request is bounded by time and response size. Only dated items
published within 48 hours are used. Future-dated, undated, off-domain, malformed and
oversized entries are rejected. Links, publication times, first-seen times, short
excerpts and content-derived IDs accompany the data. Article pages are not scraped.
The panel shows up to 60 headlines; it is not exhaustive market coverage.

In continuous paper mode, collection happens in the background so slow feeds do not
stall quote checks. News-enabled buys still need qualifying current coverage. Optional
AI also runs in the background; expired replies are rejected and cannot replace current
quote checks. See [continuous monitoring and timing limits](AGENT_PAPER_TRADING.md).

Social search covers at most eight unique configured symbols in one bounded query,
equities first. A blank crypto watchlist supplies BTC and ETH for this search, not
the entire discovered crypto catalog. It is not an X/Twitter feed, social firehose,
mempool scanner or sentiment-volume indicator. Public endpoint availability varies;
Bluesky deployments may require authentication. The app reports unavailable access
and does not try another account, scrape around restrictions, or silently fabricate data.

During development, BBC, CoinDesk and Federal Reserve feeds returned parseable RSS.
CNBC and Bluesky returned HTTP 403 from the development environment. Their accessibility
on a user's network is not established by synthetic tests. A successful feed with
zero recent entries is different from a failed request; both are shown accurately.

## How it affects proposals

The existing price-movement rules remain the baseline. With news enabled, a new
buy also needs fresh, directly matching headlines from at least two distinct news
publishers, with no configured headline risk terms. Exact duplicate headlines across
publishers count once. This is a coverage filter, not proof of independent reporting
or a forecast of price direction. It does not initiate a buy on its own.

Matching uses a small explicit company/coin-name dictionary, cashtags and selected
unambiguous uppercase tickers. It is incomplete and can misidentify a company name.
QQQ/index headlines are not applied to leveraged or inverse ETFs. The headline
filter looks for words such as bankruptcy, fraud, hacked, cuts guidance, and misses
earnings. It does not understand negation, sarcasm, financial magnitude or context.
Social posts and macro announcements do not count toward the publisher requirement.

Missing or stale coverage turns buy proposals into HOLD and discards queued virtual
buys when the next eligible quote is evaluated. News checks are repeated after model
inference and before paper processing. The news filter leaves existing exit signals
and valid quote valuations available. Independent market/session/quote checks still
apply. Long periods without qualifying coverage can produce no trades.

If the optional Ollama analyst is enabled, it receives the timestamped source context
alongside prices and your research briefs. Its structured responses must cite supplied
source IDs, and news-backed buys need at least one direct news citation. Invented IDs,
missing fields and tool requests reject the response. Excerpts are untrusted data;
they have no broker tools or authority. Citation validation establishes provenance,
not whether a model's interpretation is correct. An attached ChatGPT MCP conversation
can inspect the same context but does not become a continuous analyst automatically.

## Evaluate results before calling anything profitable

Paper results now include:

- **Average closed-trade P&L (expectancy):** realized virtual P&L divided by closed
  trades, including flat outcomes. It is an observed average, not a forecast.
- **Profit factor:** total gains on profitable closed trades divided by the absolute
  losses on losing closed trades. It is unavailable before any realized loss.
- **Maximum observed drawdown:** the greatest percentage decline from a prior virtual
  equity peak, starting with initial cash. It includes open positions at their last
  eligible bid. It is sampled at cycles, not continuously measured.

New sessions retain the peak and maximum drawdown beyond the 500-point chart window.
Older sessions reconstruct profit factor from their complete saved fill journal, but
drawdown from retained history is labeled partial. Old history is not invented.
Policy/model labels identify the run; these labels are not reproducible model hashes.
Audit receipts retain the source context used in each decision. Buy intents retain
the signal's source IDs, and those IDs follow entry fills and later closed outcomes.
These records support evaluation; there is no automatic retraining or parameter search.

The fill model accounts for bid/ask spread and 5 bps adverse slippage but excludes
fees, liquidity constraints, partial fills and settlement restrictions. Its profits
are not executable net returns. A high win rate or a short profitable paper session
does not establish an edge. Proper strategy selection still needs point-in-time data,
chronological unseen evaluation, realistic costs, drawdown assessment, and subsequent
forward observations. Failed results must not be turned into passes by lowering gates.
This feature does not replace the separate ETF Evidence Lab or change its authority.

## Data sharing

Public feed requests carry no Robinhood credentials, account numbers, balances or
orders. Optional social requests reveal the searched symbols to the public provider.
Enabling the local analyst shares source excerpts with the configured Ollama service.
When you separately enable MCP research access, the connected client can read public
sources, proposals and paper results under its own data-processing settings. Real
balances, positions, credentials and order history remain excluded from that context.
