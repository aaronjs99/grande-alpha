# Public release checklist

A public release is ready only when every required item is checked for that exact commit and artifact.

## Product and safety

- [ ] Research-only first run verified on a clean Windows profile.
- [ ] Every external connection is disclosed, off by default, and revocable.
- [ ] Live enablement, session expiry, stop, cancel, and credential forgetting are exercised.
- [ ] No installation-specific account, legal-status, balance, order, or credential data is present.
- [ ] Broker and fund disclosures are rechecked for changes.
- [ ] Market-data use and redistribution rights are reviewed.
- [ ] Community access works without checkout or an entitlement service; every Pro item is labeled
  planned until it is implemented.
- [ ] Evidence, provenance, risk, stop, privacy, and per-order consent controls remain available on
  every plan.

## Engineering

- [ ] `verify.ps1` passes.
- [ ] Dependency vulnerability audit passes or exceptions are documented.
- [ ] Source and packaged GUI smoke tests pass.
- [ ] SBOM and SHA-256 checksums are generated.
- [ ] Release archive is built from a clean commit and scanned.
- [ ] Windows executable is signed with a trusted code-signing certificate.
- [ ] Restore, migration, and uninstall/data-deletion paths are tested.

## Community and legal

- [x] The public repository has Issues enabled and private vulnerability reporting enabled; the release
  links the public support destination and private Security Advisory form.
- [ ] A monitored private conduct-enforcement destination is published.
- [ ] License, notices, third-party licenses, trademarks, screenshots, and generated brand asset are reviewed.
- [ ] A qualified reviewer assesses financial promotion, broker API terms, privacy, consumer-protection, and applicable jurisdictional obligations.
- [ ] Accessibility keyboard/focus/screen-reader checks are completed and limitations are documented.

The local `0.16.0` source application has native Windows render coverage at 900 by 1200, 1024 by 700,
1366 by 768, and 1920 by 1080. PyInstaller produces an explicitly unsigned executable candidate, while
the release process generates separate binary and cache-clean source archives, SBOM, and SHA-256 files.
The checklist remains open until those outputs are rebuilt from the exact clean release commit, scanned,
signed with a trusted publisher identity, and exercised on a clean Windows profile.
