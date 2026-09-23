# Security policy

## Supported versions

Security fixes target the latest published release. Older releases and unmerged development branches
may not receive patches.

## Report a vulnerability privately

Use a [private GitHub Security Advisory](https://github.com/aaronjs99/grande-alpha/security/advisories/new).
Do not open a public issue for a vulnerability.

Begin with a minimal, redacted description that includes the affected version, impact, and safe
reproduction conditions. Do **not** include working credentials, OAuth tokens, account identifiers,
balances, positions, order details, broker receipts, tax records, licensed market data, or an
unreviewed diagnostic export. Coordinate additional evidence only if a maintainer requests it.

If broker safety may be affected:

1. stop using GRANDE Alpha's broker features;
2. inspect account and order state directly with the broker;
3. disconnect or revoke provider access when it is safe to do so; and
4. do not attempt live reproduction.

This community project does not guarantee a response time, embargo period, bounty, or remediation.

## Security boundaries

- Research works without broker authority.
- Broker reads and real-order capabilities are separate permissions.
- Money-moving authority is bounded, held in memory, expires, and is not restored after restart.
- Credentials are stored through the Windows credential vault.
- Order requests use idempotency references and local audit receipts.
- Unknown account, quote, order, evidence, or provider state fails closed.
- Stop and cancellation controls are best effort, not exchange-side guarantees.
- GRANDE Alpha installs no background execution scheduler.

## Out-of-scope claims

GRANDE Alpha is not claimed to be penetration-tested, formally verified, suitable for institutional
use, or resilient to a compromised host, dependency, broker, market-data source, or network. An
unsigned build is not a trusted public binary. See the [release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).
