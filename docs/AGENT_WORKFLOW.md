# Bounded agent workflow

An agent roster is an optional research workflow, not a second trading authority. GRANDE Alpha does
not currently host AI workers, run a cloud coordinator, or give a language model broker credentials.
The screenshot and shared article suggest a useful division of labor, but neither establishes a
profitable strategy or a reliable execution integration.

## Roles and handoffs

| Role | May produce | Must not do |
|---|---|---|
| Research scout | Linked, timestamped public earnings and market observations | Treat social posts as verified data or access broker write tools |
| Thesis analyst | A structured candidate thesis with assumptions and counterarguments | Convert prose directly into an order |
| Evidence reviewer | Source, timing, licensing, replay, and forward-observation findings | Mark missing evidence as passing or approve its own thesis |
| Operations monitor | Local health, stale-data, and broker-disconnection alerts | Retry an uncertain order or silently restart authority |

These roles may pass a *proposal* to the existing deterministic planner. The planner alone chooses
whether there is an eligible candidate; the existing execution, risk, account, evidence, and broker
checks remain independent. A model response is never a grant, a broker quote, or proof of a fill.

## Proposed packet contract

Every proposal needs a unique identifier, creation time with timezone, symbol, source links,
observation and publication times, licensing status, thesis, counterarguments, and explicit missing
fields. Missing, stale, ambiguous, or unlicensed fields leave the proposal in research only. A
reviewer must be able to trace each factual claim to its source. The original sources and model
output remain separate so a persuasive summary cannot rewrite the evidence.

An operator can review research in the desktop app. Neither a group chat nor a recurring cloud task
may call Robinhood's placement or cancellation tools through this workflow. If an advisory model is
added later, its provider, data-sharing terms, cost ceiling, retention policy, evaluation set, and
read-only tool permissions require a separate product decision and explicit opt-in.

## Connection boundary

Robinhood's [Agentic Trading guide](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
lists desktop, CLI, and other MCP clients. A CLI is one supported client, not the broker protocol.
The product's own MCP adapter remains the only broker boundary. Adding five AI roles would not fix
an OAuth, account-read, disconnect, or transport-lifecycle bug; diagnose those separately.

## Current status

This page is an architecture contract, not a launched agent service. The current code has a
deterministic strategy and broker-isolated research components. No Grok account, AI subscription,
cloud host, periodic routine, or additional broker permission has been configured by this document.
The remaining activation requirements are listed in [Getting to autonomy](GETTING_TO_AUTONOMY.md).
