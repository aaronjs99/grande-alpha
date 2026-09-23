# Roadmap to 1.0.0

GRANDE Alpha uses **1.0.0**—the standard semantic-version form—as a quality and support threshold,
not a deadline or marketing label. The project reaches 1.0.0 only when every required gate below is
implemented, documented, and verified for the exact release candidate.

## Gate 1: product contract

- [x] Research, broker-read, shadow, supervised, and autonomous layers are explicitly separated.
- [x] Community access and never-paywalled safety controls are defined.
- [x] Privacy, support, security, contribution, and conduct policies are published.
- [ ] Supported workflows are reviewed as one coherent user journey with no dead or contradictory path.
- [ ] Every UI term, permission, failure state, and recovery action has user documentation.

## Gate 2: research integrity

- [x] Strategy fingerprints bind material research and execution settings.
- [x] Evidence policy, chronological holdout, provenance, costs, and expiration are versioned.
- [x] Synthetic and observed-data claims are separated.
- [ ] A legally usable reference dataset and reproducible benchmark are published or independently
  reproducible by users.
- [ ] Evidence and runtime-parity tests cover every production-supported strategy and route.
- [ ] Independent review confirms that certificates cannot be promoted through a bypass or stale path.

## Gate 3: execution safety and recovery

- [x] Authority is bounded, in memory, expiring, and fail-closed.
- [x] Supervised orders require exact per-ticket confirmation.
- [x] Ambiguous placement outcomes are quarantined instead of retried.
- [x] Stop/cancel is scoped to GRANDE-owned orders and requires explicit confirmation.
- [ ] Crash, network-loss, partial-fill, provider-timeout, and restart recovery are exercised against
  the exact supported provider contract in a non-production test environment.
- [ ] Independent safety review covers order lifecycle, reconciliation, settlement, and data freshness.
- [ ] Operator-visible recovery guidance is validated through usability tests.

## Gate 4: product quality

- [x] Automated lint, tests, compilation, dependency audit, and package build run in CI.
- [x] Portrait, constrained-landscape, standard, and wide layouts have regression coverage.
- [ ] Keyboard navigation, focus order, scaling, contrast, and screen-reader behavior meet the adopted
  accessibility target.
- [ ] Configuration and database migrations are tested from every supported release line.
- [ ] Install, upgrade, repair, uninstall, data export, and data deletion pass on clean supported
  Windows profiles.
- [ ] Performance budgets and long-running memory/resource tests are enforced.

## Gate 5: secure distribution

- [x] Source and unsigned candidate paths are distinguished.
- [x] Release automation produces versioned source and binary candidates, checksums, and an SBOM.
- [ ] Release artifacts are reproducibly associated with a clean commit and reviewed dependency lock.
- [ ] Windows executable and installer are signed with a trusted publisher identity and timestamp.
- [ ] Download, installation, and update behavior are verified on clean Windows systems.
- [ ] Vulnerability-response ownership, dependency-update policy, and release revocation procedure are
  documented and exercised.

## Gate 6: provider, legal, and operational readiness

- [ ] Written provider approval covers the intended public product and distribution model.
- [ ] Market-data acquisition, storage, use, and redistribution rights are documented for every source.
- [ ] Qualified review covers financial-promotion, consumer-protection, privacy, tax-record, and
  jurisdictional obligations applicable to distribution.
- [ ] Support scope, response ownership, incident handling, and service limitations are published.
- [ ] A clean public release contains no account-, operator-, installation-, or credential-specific data.

## Gate 7: stable 1.0 interface

- [ ] Configuration, database, CLI, diagnostic, receipt, evidence-certificate, and plugin/integration
  compatibility promises are written and tested.
- [ ] Deprecation and migration policy is active.
- [ ] Backup and forward/rollback compatibility behavior is documented.
- [ ] Release notes enumerate every public interface and known limitation.
- [ ] The exact 1.0.0 commit passes the complete [public release checklist](RELEASE_PROCESS.md#public-release-checklist).

## What 1.0.0 will not mean

Version 1.0.0 will not certify profitability, guarantee uptime or fills, provide individualized
financial/legal/tax advice, or eliminate market, broker, network, operating-system, or model risk.
It will mean that the product contract is stable, the published workflows are supportable, and the
release evidence satisfies the project's declared engineering and distribution gates.
