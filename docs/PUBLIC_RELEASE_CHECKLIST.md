# Public release checklist

A public release is ready only when every required item is checked for that exact commit and artifact.
For version 1.0.0, every mandatory item in the [roadmap to 1.0.0](ROADMAP_TO_1_0.md) must also pass.

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
- [ ] Product behavior matches the published [product contract](PRODUCT_CONTRACT.md).

## Engineering

- [ ] `verify.ps1` passes.
- [ ] Dependency vulnerability audit passes or exceptions are documented.
- [ ] Source and packaged GUI smoke tests pass.
- [ ] SBOM and SHA-256 checksums are generated.
- [ ] Release archive is built from a clean commit and scanned.
- [ ] Package, application, tag, artifacts, checksums, SBOM, and release notes report one version.
- [ ] Windows executable is signed with a trusted code-signing certificate.
- [ ] Restore, migration, and uninstall/data-deletion paths are tested.

## Community and legal

- [ ] The public repository has Issues enabled and private vulnerability reporting enabled; the release
  links the public support destination and private Security Advisory form.
- [ ] A private conduct-reporting path is published in `CODE_OF_CONDUCT.md`.
- [ ] Monitoring and response ownership for private conduct reports are documented and exercised.
- [ ] License, notices, third-party licenses, trademarks, screenshots, and generated brand asset are reviewed.
- [ ] A qualified reviewer assesses financial promotion, broker API terms, privacy, consumer-protection, and applicable jurisdictional obligations.
- [ ] Accessibility keyboard/focus/screen-reader checks are completed and limitations are documented.

A release candidate must preserve native Windows render coverage at 900 by 1200, 1024 by 700, 1366
by 768, and 1920 by 1080. PyInstaller output remains an unsigned candidate until signed with a trusted
publisher identity. The checklist remains open until source and binary archives, SBOM, checksums,
scans, signatures, and clean-profile acceptance tests are regenerated for the exact release commit.
