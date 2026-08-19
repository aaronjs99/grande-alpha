# Security policy

## Supported version

Only the latest tagged community-preview release receives security fixes.

## Report a vulnerability

Report vulnerabilities through a
[private GitHub Security Advisory](https://github.com/aaronjs99/grande-alpha/security/advisories/new).
Do not open a public issue containing a vulnerability, credential, account identifier, position,
broker receipt, or live-order detail. Do not include working credentials or unnecessary account data
even in the private report; begin with a redacted description and coordinate any sensitive evidence
only if a maintainer requests it. If broker safety may be affected, stop using broker features,
disconnect the app in Robinhood when safe to do so, and verify account and order state directly with
the broker. A private report does not guarantee response time or remediation.

## Security model

- Research features work without broker authority.
- Broker read access and real-order automation are independent permissions.
- Live authority is in memory, expires, is not restored after restart, and is bounded by account identity and numeric limits.
- Credentials are stored through the OS credential vault.
- Order requests have idempotency references and local receipts.
- Stop and cancellation are best-effort controls, not exchange-side guarantees.

## Out of scope claims

No claim is made that the app is penetration-tested, formally verified, suitable for institutional use, or resilient to a compromised host, dependency, broker, market-data source, or network. Release binaries should be code-signed; unsigned preview builds may trigger Windows warnings.
