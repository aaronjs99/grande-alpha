# Product contract

This document defines the durable public contract for GRANDE Alpha. It applies to source checkouts,
packaged builds, Community access, and any future paid plan. A release may add stricter safeguards,
but it must not silently weaken these boundaries.

## Product purpose

GRANDE Alpha is a local-first Windows desktop product for:

- researching leveraged-ETF strategies with reproducible inputs and explicit assumptions;
- comparing candidates with cost stress, chronological separation, and evidence gates;
- observing a supported broker connection without submitting orders in live shadow mode; and
- conducting separately enabled, bounded execution experiments when every applicable technical,
  provider, account, consent, and jurisdictional requirement is satisfied.

GRANDE Alpha is not an investment adviser, broker, exchange, custodian, market-data vendor, tax
service, legal service, or promise of investment performance.

## Product layers

| Layer | Broker required | Can submit an order | Durable guarantee |
|---|---:|---:|---|
| Local research | No | No | Usable without an account or external connection |
| Remote market-data research | No | No | Separate opt-in with source and licensing disclosure |
| Broker diagnostics | Yes | No | Read-only facade blocks review, placement, and cancellation |
| Live shadow | Yes | No | Current observations produce fictional execution receipts only |
| Supervised experiment | Yes | Yes | Bounded session and fresh consent for every reviewed ticket |
| Autonomous authority | Yes | Yes | Exact evidence, runtime parity, account, route, risk, and session gates |

No layer may infer permission from a lower layer. Connecting a broker does not enable orders. Enabling
an order capability does not create authority. A research result does not place an order.

## Safety invariants

Every supported distribution must preserve these invariants:

1. **Fail closed.** Missing, stale, ambiguous, unsupported, or mismatched account, quote, order,
   evidence, session, or provider state blocks new order activity.
2. **No silent authority.** Money-moving authority is finite and exact. The supervised path does not
   restore a grant after restart; the separate mixed standing runner may recover only a sole active,
   unexpired, unchanged durable authorization after exact account and process-lease checks.
3. **Exact scope.** Authority binds the account, symbols, strategy fingerprint, route, expiry, and
   numeric risk ceilings shown to the operator.
4. **Separate consent.** Supervised placement requires a fresh, transaction-specific confirmation
   after broker review.
5. **No implicit cancellation.** Disconnect, capability changes, credential removal, and exit do not
   silently cancel orders. Cancellation uses a separate, exact preview and confirmation.
6. **Broker truth wins.** Local state never overrides authoritative account, order, fill, buying-power,
   session, halt, or venue state.
7. **No hidden scheduler.** The product installs no unattended execution schedule or background
   money-moving service.
8. **No paywalled safety.** Evidence, provenance, risk limits, stop/cancel, privacy, credential, and
   consent controls remain available on every plan.

## Evidence contract

Research artifacts must disclose their source, time span, resolution, hash or durable identity,
strategy fingerprint, execution assumptions, costs, evaluation split, and failed gates. Synthetic,
interpolated, incomplete, or unlicensed inputs cannot be represented as observed deployable evidence.

An evidence certificate states that one exact candidate passed one versioned policy on one bound
dataset and runtime contract. It expires, can be invalidated by material configuration changes, and
does not predict future performance or authorize an order by itself.

## Privacy and data ownership

- Core research is local and requires no GRANDE Alpha account.
- The product operates no first-party telemetry, advertising, or cloud-sync service unless a future
  release introduces one through a new, documented, default-disabled permission.
- Credentials use the operating-system credential vault and must not be written to project files,
  configuration JSON, diagnostics, or the research database.
- Diagnostics are user-initiated and must be reviewed before sharing.
- Users control local retention, backup, export, and deletion subject to their own recordkeeping duties.

See [Privacy](../PRIVACY.md) for the complete data inventory.

## Distribution contract

A public release must provide a supported installation path, versioned artifacts, checksums, an SBOM,
license and notice files, security and support routes, migration behavior, and truthful signing status.
An unsigned executable is a build candidate, not a trusted public binary.

Broker connectivity may be distributed only when the publisher has the provider permissions required
for the exact product, users, order flow, branding, data handling, and distribution model. A source
adapter does not establish that permission or imply provider endorsement.

## Plans and commercial features

Community access is the complete local safety and research baseline. A future paid plan may add
convenience, organization, collaboration, scale, managed infrastructure, or advanced exports. It may
not convert failed evidence into passing evidence, broaden authority without consent, conceal material
risk, or remove a user's access to their local records.

## Compatibility before 1.0.0

The `0.x` line may change configuration, database, CLI, or integration contracts when needed to reach
the 1.0 product standard. Such changes require changelog entries, safe migrations where persisted data
is involved, and prominent documentation. Version 1.0.0 establishes the first stable public contract
described in [Versioning](VERSIONING.md).
