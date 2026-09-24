# Local AI chat and saved Agent configuration

Open **Agent · Stocks + Crypto → Chat + AI connections**. With Ollama running and
an installed model selected (for example `qwen2.5:3b`), type a question and click
**Send to local AI**. No tunnel, API key or research MCP permission is needed for
this local chat. **Choose local AI model** opens the existing setup controls. The
background-analysis checkbox can remain off when using chat by itself.

Examples:

- “Explain the paper losses. How much is realized, and what could spread and slippage explain?”
- “Pause new paper buys while we examine these results.”
- “Limit adaptive paper trading to two positions and 20% virtual exposure.”
- “Have the AI examine transaction costs and flag unreliable social claims.”

Replies use a snapshot of the current paper portfolio, recent simulated fills,
up to eight recent instrument observations and their available headline context.
The chat has no independent web search and does not continuously monitor the session.
It must distinguish missing/stale data from evidence and cannot establish future returns.
Real account balances, account identifiers, broker positions, credentials and order
tools are not included in the chat context. User-entered messages and research
prompts are included; do not put secrets in them.

The reply appears as plain text. If the model proposes settings, GRANDE displays
the old and proposed values separately. Only **Apply suggested changes** applies
them. Settings changed since the question invalidate the proposal. Applying a
proposal preserves the current portfolio and its recorded P&L; it never resets
losses, starts a new session, or sends a broker order.

| Setting | Effect |
| --- | --- |
| Team research directions | Replaces the saved team prompt; guides future local AI analysis. In adaptive mode the AI remains advisory, while price rules decide paper trades. |
| Pause new paper buys | Blocks new virtual buys and removes pending virtual buys. Existing holdings retain their normal exit rules. Applies to all paper modes. |
| Adaptive maximum positions | 1–4 held or pending entries across both market workers. |
| Adaptive maximum exposure | 5–40% of starting virtual cash at entry cost. This cap may prevent a buy if the configured cash per buy is larger. |

Manual paper controls below the chat apply and save immediately. Position/exposure
limits affect adaptive new entries only; reducing them does not sell existing
holdings. Fixed stop, target, drawdown, quote eligibility and broker permission
checks cannot be changed by a chat reply. These controls do not guarantee profits.

Only one chat request runs at a time. A cancel button and elapsed timer stay
available; requests have a 60-second overall deadline. An unavailable model,
timeout, incomplete or invalid JSON response produces a visible error and applies
no proposal. Stop agent, STOP + CANCEL, Disconnect and Exit cancel an active reply.
The six cards, charts and activity log retain their dashboard layout.

## Configuration persistence

Valid Agent selections save automatically after a short editing pause (500 ms),
on explicit Save/Start, and when closing the app. Saved fields include both
watchlists, saved-scan identifier, quote interval, model and background-AI choice,
team/worker prompts, strategy, news/social/X monitoring choices, price source,
starting virtual cash, cash per buy, demo repeat and paper entry controls.

Settings live in `grande_alpha-agent-settings.json` beside the local audit database.
The file is versioned, validated and replaced atomically. Invalid edits leave the
previous saved configuration intact and show an error. Unreadable or unsupported
saved files are kept; the app shows defaults with a recovery message.

Launching restores configuration only. It does not start trading, reconnect the
broker, enable MCP research access or restore live authority. Paper records remain
in the existing paper ledger; chat conversation text lasts until the app closes.
Applied directions and controls persist. Theme and main application preferences
continue using their existing stores; X credentials stay in the system keychain.

Tests cover restart recovery, invalid drafts, disk-save failure, pending-buy
cancellation, continued exits, reduced exposure, stale proposals, AI failures and
request cancellation. Model responses in automated tests are fixtures, not proof
of reasoning quality or investment performance.
