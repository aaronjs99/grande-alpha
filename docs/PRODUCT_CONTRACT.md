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

## Compatibility before 1.0.0

The `0.x` line may change configuration, database, CLI, or integration contracts when needed to reach
the 1.0 product standard. Such changes require changelog entries, safe migrations where persisted data
is involved, and prominent documentation. Version 1.0.0 establishes the first stable public contract
described in the [versioning policy](RELEASE_PROCESS.md#versioning-policy).

## Community and Pro plans

### Current release

GRANDE Alpha starts on the **Community** plan. Community costs `$0` and is built into the local
application; it does not require a GRANDE Alpha account, license key, payment method, checkout, or
entitlement server. The current release has no paid-plan activation path.

![Community and Pro plan dialog](images/product/community-and-pro-plans.png)

The Community plan includes the implemented product:

- local replay, configuration comparison, and the nine-action policy lab;
- data-readiness, provenance, cost-stress, walk-forward, and evidence tools;
- live shadow and local receipts; and
- every safety boundary, risk limit, stop control, privacy control, and per-order consent step.

Broker and market-data providers can impose their own eligibility rules, terms, subscriptions, or
charges. Those provider conditions are separate from the GRANDE Alpha product plan.

### Pro direction

The desktop can advertise a **Pro — Coming soon** direction. The current Pro card lists only planned
convenience and scale improvements, such as expanded experiment organization, extended analytics,
and additional report exports. These are not implemented entitlements, not a purchase offer, and not
promises about a specific delivery date or price.

GRANDE Alpha will not use a paid tier to weaken or hide:

- evidence and data-provenance checks;
- risk limits and fail-closed execution controls;
- stop, cancellation, privacy, and credential controls; or
- transaction-specific disclosures and consent.

Paid value belongs in convenience, organization, scale, and optional services. A plan can never turn
failed evidence into passing evidence, unlock an otherwise unsafe route, or imply profitability.

### Optional product-information link

Distributors may set the `GRANDE_ALPHA_UPGRADE_URL` environment variable to an HTTPS product-information
page. When configured, **View Pro updates** opens that page only after the user clicks it. The link is
not treated as checkout and does not change local access. Invalid, non-HTTPS, or credential-bearing
URLs are ignored.

If no URL is configured, the button reads **Pro updates coming soon** and remains disabled. GRANDE
Alpha sends no telemetry merely because the plan dialog is opened.

### Entitlement model

The application exposes an explicit local entitlement snapshot:

- active plan: `community`;
- source: built-in local Community access;
- checkout available: `false`; and
- paid entitlement available: `false`.

All implemented plan-controlled feature IDs belong to Community. Planned Pro feature IDs remain
unavailable until a later release implements and documents a real entitlement boundary. Provider
permissions, market-data rights, safety checks, and trading consent are not product entitlements.

The same snapshot is available without a GUI:

```powershell
.\cli.ps1 plans
.\cli.ps1 plans --json
```
