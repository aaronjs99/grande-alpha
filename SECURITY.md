# Security policy

## Supported version

Only the latest tagged community-preview release receives security fixes.

## Report a vulnerability

Do not open a public issue containing a vulnerability, credential, account identifier, or live-order detail. Until a private security contact is published, stop using broker features, disconnect the app in Robinhood, and retain only redacted evidence. A public release is blocked until the maintainer adds a monitored private reporting address here.

## Security model

- Research features work without broker authority.
- Broker read access and real-order automation are independent permissions.
- Live authority is in memory, expires, is not restored after restart, and is bounded by account identity and numeric limits.
- Credentials are stored through the OS credential vault.
- Order requests have idempotency references and local receipts.
- Stop and cancellation are best-effort controls, not exchange-side guarantees.

## Out of scope claims

No claim is made that the app is penetration-tested, formally verified, suitable for institutional use, or resilient to a compromised host, dependency, broker, market-data source, or network. Release binaries should be code-signed; unsigned preview builds may trigger Windows warnings.
