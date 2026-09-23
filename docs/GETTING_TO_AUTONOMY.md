# Getting to unattended operation

This is a development/activation handoff, not an assertion that the product is ready for unattended
real-money trading. Run `grande-alpha-cli engine readiness` for the current offline inventory.
Local evidence presence is separate from requested-limit validation and broker verification.
The default inventory is scoped to the installed strategy. Use `engine readiness --workflow earnings`
for PEAD requirements, and `--setup FILE` to acknowledge recorded planning amounts. A complete
`--policy FILE` can be checked offline without granting authority. Optional AI/earnings work is not
a universal prerequisite for the existing deterministic strategy.

## Implemented versus missing

| Area | Implemented | Still required |
|---|---|---|
| Broker connection | Native Streamable HTTP MCP, pinned contract, exact ticket review and symbol checks | Provider-observed unattended cancellation qualification |
| Runtime | Mixed stock/ETF execution core, foreground runner, multi-day grants, exact-scope crash recovery, duplicate-process exclusion, durable idempotency, closed-session waiting | Always-on deployment and provider-observed qualification |
| User control | Explicit limits, finite standing sessions or exact-ticket approvals, durable local stop | Remote alerts and provider-observed emergency recovery; cancellation remains attended |
| Earnings strategy | Causal PEAD screen, mixed allocator, raw provider capture, historical replay and append-only forward recorder | Licensed observations and a passing exact-candidate certificate |
| AI | No AI decision stage claims | Selected provider, constrained advisory interface, independent risk enforcement and evaluation |
| Hosting | No GUI needed for CLI; no scheduler; local persistent inbox and terminal alerts | Secret storage, resource budgets, restart/outage qualification; GUI/toast alerts not implemented |

The CLI is an operating interface, not the broker protocol. Robinhood's
[official setup guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
lists desktop and CLI clients. A cloud chat is not an always-on execution host, and local uncommitted
changes do not automatically reach another chat, repository checkout, or deployed service.

## One consolidated operator input checklist

1. **Scope:** earnings-driven individual US stocks or the existing leveraged-ETF strategy; allowed
   universe, long-only versus any other exposure, regular-hours versus additional sessions, order
   types, whether positions may remain overnight, and what should happen at a stop/expiry.
2. **Risk:** total allocated trading capital, per-order cap, aggregate exposure cap, daily gross
   notional cap, maximum daily loss threshold, maximum trades/rate, spread/quote-age limits, and
   finite authority duration. These values must be deliberately selected, not invented from an old
   casual budget remark. A loss threshold cannot guarantee a maximum realized loss.
3. **Broker:** confirm the intended Agentic account through local OAuth. Another trader's setup is
   not a prerequisite: use Robinhood's official connection guide and the actual provider tool
   descriptions. A sanitized third-party configuration is optional comparison material only.
   Do not share passwords, OAuth tokens, cookies, or private account statements in chat.
   A profitable anecdote does not establish the broker contract or validate this implementation.
4. **Optional AI and strategy-specific data:** an AI provider is needed only if an AI advisor is
   selected. PEAD requires point-in-time earnings/consensus/quote data with appropriate usage rights;
   the existing deterministic strategy does not require earnings data or an AI subscription.
   Set an operating-cost limit before any paid-provider evaluation. Credentials belong in the
   configured secret store, never source files.
5. **Execution host:** choose an always-on personal machine or a cloud host and monthly cost cap.
   Confirm permitted deployment access. Development chat placement alone does not select hosting.
6. **Alerts and response:** choose an alert destination and a person who can respond to broker
   outages, authentication expiry, unknown fills, and residual exposure. Specify escalation behavior
   when no one responds. Do not assume stopping a process closes positions.
7. **Publication:** identify the repository/branch to receive the changes and authorize publishing
   them when ready. Never upload local policies, tokens, broker databases, or private research data.

For the first reply, the high-level choices and budget ceilings suffice. Exact technical limits can
then be assembled into one readable policy for deliberate approval; users do not need to invent
fingerprints, database fields, or broker parameter names themselves.

## Plain-language setup and configurable boundaries

Trading capital is the real money allocated to the experiment. Operating budget is a separate
monthly allowance for AI calls, licensed data and hosting. Neither amount should be inferred from
the other, and a planning budget is not an enforced billing cap or permission to buy subscriptions.

An **always-on host** is the machine running the trading engine, even when the desktop interface is
closed. It may be a dedicated personal computer or a rented cloud computer. A sleeping laptop or a
development chat is not an always-on host. An **alert destination** is a verified email, phone or
messaging channel where a person receives failures and can respond. No destination should be
guessed from unrelated personal information, and none is configured by simply naming a channel.

The default is offline research, with optional shadow observation after broker consent. There is
no maximum-profit preset: evaluation must consider costs, losses and uncertainty, and may prefer
no trade. Capital, loss tolerance and asset scope need deliberate approval before live use.

Currently configurable session settings include duration, per-order amount, total exposure, daily
gross notional, loss threshold, order count/rate, spread and quote age. These remain subject to
runtime/provider constraints. A loss threshold requests a response; it cannot guarantee the final
loss will stay below that amount, particularly across gaps or outages.

Arbitrary stock universes, shorting, options and overnight live routes are **not** unlocked by
configuration in this release. Current live tickets remain restricted to the supported TQQQ/SQQQ
regular-hours route. Research settings that permit a scenario do not authorize the same live trade.
Broader universes and multi-day earnings strategies require additional execution/risk integration
and qualification. Keep unsupported settings visibly unavailable instead of accepting them silently.

## Work that remains engineering responsibility

Daily loss recovery now persists observed account-value peaks and a same-day stop latch. Offline
tests cover process restart, re-arming, recovered balances, stricter limits, missing history, failed
persistence, and trading-day rollover. These fabricated-account tests do not establish broker-side
recovery or qualify unattended operation.
The [standing engine](UNATTENDED_ENGINE.md) now has an explicit placement-delegation path, pinned
provider metadata, local stop monitoring, and no-retry recovery tests. These are implementation
results, not a claim that all deployment or strategy qualification work is complete.

Getting the inputs above does not itself make the system ready. Engineering still owns the AI
adapter, PEAD replay/portfolio integration, broader equity risk model, provider-contract conformity,
data/evidence qualification, and deployment/recovery testing. Do not present those as user failures.

The separate [mixed production qualification contract](PRODUCTION_QUALIFICATION.md) binds current
earnings provenance, exact broker review, a single-process lease, multi-day recovery and replay
evidence. Those mechanisms are implemented, but passing real-world evidence has not yet been
collected.

## Mixed live runner

The product now exposes the previously internal mixed engine through three commands:

```powershell
.\cli.ps1 engine autonomous-template
.\cli.ps1 engine autonomous-readiness --candidate C:\private\candidate.json --qualification C:\private\qualification.json --authorization C:\private\authorization.json --earnings-database "$env:LOCALAPPDATA\GRANDEAlpha\earnings.db" --source C:\private\current-research.json
.\cli.ps1 engine run-autonomous --candidate C:\private\candidate.json --qualification C:\private\qualification.json --authorization C:\private\authorization.json --earnings-database "$env:LOCALAPPDATA\GRANDEAlpha\earnings.db" --source C:\private\current-research.json --connect
```

`autonomous-readiness` is offline and never grants authority. `run-autonomous` checks the exact
Agentic account and pinned provider contract, then requires one exact terminal phrase for the first
activation. A crashed process may recover only the sole active, unexpired authority with the
identical candidate digest; a normal stop revokes it. The source file is reloaded on every cycle and
must be atomically refreshed by the qualified current-data collector.

Authorization is independent of market hours. The process may be started on any day and remains
alive while the exchange route is closed, but the current stock-capable route creates only
regular-hours market/GFD tickets. It does not claim weekend trading or silently queue market orders.
No scheduler or background service is installed.

Before activation, require reproducible offline tests, chronological strategy evidence, realistic
costs, shadow comparison, provider-observed partial/filled/cancelled/unknown-order recovery, and
audited authority/stop behavior on the selected host. A supervised small real-money test requires
separate deliberate approval; it is not implied by a request to finish the code.

Public freemium distribution adds separate provider, market-data licensing, privacy, support,
security, and jurisdiction-specific review requirements. Personal pilot readiness is not public
product readiness. Neither readiness label is a promise of profit.
