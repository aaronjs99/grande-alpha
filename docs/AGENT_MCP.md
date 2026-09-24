# Agent prompts and MCP

The Agent desk runs two concurrent research workers: **Equities** and **Crypto**.
Each discovers candidates, reads quotes, runs its analyst, and checks data quality.
The cards show real progress. Scout, Analyst and Risk describe the shared workflow;
Execution remains locked. These are not six independently authorized trading agents.

Workers run concurrently, including separate Ollama requests when eligible observations
exist. The Robinhood transport still serializes its session calls. Each worker has a
35-second deadline; a worker error does not discard the other market's results. Cycles
never overlap. STOP cancels both workers, discards partial results, and revokes MCP access.

**This change adds research orchestration and AI connectivity, not automatic real-money
trading.** Prompts, extra workers and an MCP connection do not establish profitable
performance or authorize orders. Remaining live integration is tracked in
[AGENT_EXECUTION_JOURNAL.md](AGENT_EXECUTION_JOURNAL.md).

## Give the workers prompts

1. Open **Agent · Stocks + Crypto → Configure universe and AI**. Set the universe and
   optional installed Ollama model. **Save research setup** keeps it for this app session.
2. Open **Prompts + AI connections** beside Start/Stop. Enter a team brief and separate
   Equities/Crypto briefs (up to 2,000 characters each).
3. Click **Apply prompts · next cycle**, then **Start agent analysis** if stopped.

The current cycle keeps its original prompts; changes take effect together on the next
cycle. Prompts guide the local analyst only when Ollama is enabled. With **Rules baseline**,
they are context for an external MCP client, and do not alter deterministic signal rules.
The model still returns only strictly validated proposals. Data checks are applied again
after inference and after both workers complete. A bad model response becomes HOLD.
Prompts cannot change risk thresholds, cash limits, credentials, or live permissions.
Applied prompts are recorded with local research receipts; do not include secrets.

![Synthetic prompt controls, no real account information](images/agent-mcp-prompts.png)

## ChatGPT Astra setup button

Open **Prompts + AI connections → ChatGPT Astra setup** for an offline, scrollable guide.
It includes official OpenAI links and buttons to copy instructions, this installation's
server command, and a read-only verification prompt. Opening the guide does not enable
MCP, install software, collect API keys, create a tunnel, or connect an account.

The guide describes OpenAI's **Secure MCP Tunnel** route for ChatGPT, including the
separate developer-mode and Platform tunnel prerequisites, followed by selecting Astra
when available. A tunnel must run on the same computer as GRANDE and be associated with
the intended ChatGPT workspace. Setup and model availability depend on your OpenAI
account. This route has not been authenticated or tested against your account.

The copied local-client JSON is not a ChatGPT server URL. The server command uses the
running Python interpreter and the absolute bridge path, so install GRANDE in that
environment first. Packaged executables show a source-installation note instead of an
invalid Python command. The copy buttons only change the clipboard.

![Offline ChatGPT Astra setup guide, with synthetic example paths](images/chatgpt-astra-setup.png)

Official sources reviewed September 24, 2026:

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Connect and test a ChatGPT plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Model availability and selection](https://learn.chatgpt.com/docs/models)

## Connect a compatible AI application

MCP means **Model Context Protocol**. This build exposes a **local stdio MCP server**.
Use a client that can launch a local executable and configure stdio servers. It does not
connect universally to every AI product: clients requiring a hosted HTTPS MCP URL cannot
use this local configuration directly; the ChatGPT setup guide explains the separate
Secure MCP Tunnel option. No public endpoint, tunneling service, or cloud
account is created, and the app does not install an AI client or download a model.

From a source checkout, install the updated entry points once:

```bash
.venv/bin/python -m pip install -e .
```

Then:

1. Keep GRANDE open, connect Robinhood, and save your research setup.
2. In **Prompts + AI connections**, read the sharing description and check
   **Enable research MCP for this app session**.
3. Click **Copy MCP client configuration**. Paste the resulting `mcpServers` entry
   into the compatible AI client's MCP settings (some clients use a different wrapper).
   The copied paths point to this computer's Python, source tree and local bridge.
4. Restart/reload that client's MCP connection if required. The client launches the
   stdio process; do not run the server as a second desktop app.
5. Ask the connected AI, for example:
   “Set the team brief to compare observed movements and spread costs. Use AAPL and
   MSFT for stocks and BTC and ETH for crypto. Start research, then explain which
   observations are current and why candidates are blocked. Do not place trades.”

The AI client supplies the conversational model. An ongoing local analyst still requires
Ollama enabled in GRANDE; an attached chat alone does not become its continuous analyst.
No model/provider API keys are stored by this MCP bridge. A client's provider may process
whatever research data it receives according to that client's settings.

Example configuration shape (use the copied actual paths instead):

```json
{
  "mcpServers": {
    "grande-alpha": {
      "command": "/absolute/project/.venv/bin/python",
      "args": ["-m", "grande_alpha.agent_mcp", "--bridge", "/absolute/data/agent-mcp.db"],
      "env": {"PYTHONPATH": "/absolute/project/src"}
    }
  }
}
```

The standalone packaged desktop does not supply a Python interpreter for stdio clients;
use the source environment for this integration. On Windows, the equivalent Python is
`.venv\Scripts\python.exe`. On macOS, launch the installed desktop with
`.venv/bin/grande-alpha`.

## Available tools

| Tool | Effect |
| --- | --- |
| `get_research_context` | Current worker state, prompts, universe, timestamped numeric observations and proposal labels |
| `set_research_brief` | Change the team, equity or crypto research prompt for the next cycle |
| `configure_research_universe` | While stopped, change research symbols; clears the previously selected saved scan |
| `start_research` | Start the two workers using saved desktop settings; broker must already be connected |
| `stop_research` | Stop analysis; MCP stays enabled so the client can restart research |

The `review_markets` MCP prompt helps the client review observed data and uncertainty.
There are no order tools, arbitrary shell/file tools, login tools, budget setters, or
live-authority setters. Exported context omits account identifiers, balances, positions,
orders, credentials, provider errors, and free-form model reasons. Quote age and data-check
labels must be considered together; a completed observation may already be stale.

## Stop or revoke access

- Uncheck **Enable research MCP** to revoke AI access; research already running continues
  until stopped. Revocation cannot retract data already shared with an AI client.
- **Stop agent**, **STOP + CANCEL**, **Disconnect**, broker-permission revocation, and
  **Exit** revoke MCP access and stop research. Re-enable it deliberately before a new
  client request can start research. MCP cannot re-enable itself.
- Permission is not saved across app launches. Every enable creates a new session and
  discards old commands. The bridge has a bounded queue and expiring requests; it cannot
  replay old commands after reconnect. A stopped/crashed desktop cannot serve requests.
- A client timeout leaves the command outcome uncertain; inspect the desktop before
  retrying. Research controls cannot send a financial transaction.

The bridge is a separate local SQLite mailbox in the app's data directory, accessible
only to the local OS account on POSIX. It is not an authentication boundary against other
programs already running as that same user. No network listener is opened. Active records
are deleted on normal revocation; after a crash, expired records are removed on the next
successful connection/enable. Local cycle receipts follow the existing retention policy.

## Verification

Tests use fake brokers, a simulated market clock, mocked model HTTP, and actual MCP
in-memory and stdio clients. They exercise concurrent workers, prompt boundaries, malformed
commands, default-off access, queue bounds, stale sessions, STOP revocation, and desktop
shutdown. No provider login, live model, or real order is used. Client-specific UI setup
still depends on the AI application chosen by the user.

Primary protocol reference: [official MCP Python SDK v1 documentation](https://py.sdk.modelcontextprotocol.io/v1/).
The repository intentionally retains its existing `mcp>=1.29,<2` dependency contract.
