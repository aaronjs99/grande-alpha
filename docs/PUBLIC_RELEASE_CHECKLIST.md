# Public release checklist

A public release is ready only when every required item is checked for that exact commit and artifact.

## Product and safety

- [ ] Research-only first run verified on a clean Windows profile.
- [ ] Every external connection is disclosed, off by default, and revocable.
- [ ] Live enablement, session expiry, stop, cancel, and credential forgetting are exercised.
- [ ] No personal account, tax, immigration, balance, order, or credential data is present.
- [ ] Broker and fund disclosures are rechecked for changes.
- [ ] Market-data use and redistribution rights are reviewed.

## Engineering

- [ ] `verify.ps1` passes.
- [ ] Dependency vulnerability audit passes or exceptions are documented.
- [ ] Source and packaged GUI smoke tests pass.
- [ ] SBOM and SHA-256 checksums are generated.
- [ ] Release archive is built from a clean commit and scanned.
- [ ] Windows executable is signed with a trusted code-signing certificate.
- [ ] Restore, migration, and uninstall/data-deletion paths are tested.

## Community and legal

- [ ] Public repository, issue tracker, private security address, support destination, and conduct-enforcement address are published.
- [ ] License, notices, third-party licenses, trademarks, screenshots, and generated brand asset are reviewed.
- [ ] A qualified reviewer assesses financial promotion, broker API terms, privacy, consumer-protection, and applicable jurisdictional obligations.
- [ ] Accessibility keyboard/focus/screen-reader checks are completed and limitations are documented.

The local `0.6.0` build is a community-preview release candidate, not a claim that these external launch gates are complete. On the 2026-08-09 build host, PyInstaller produced the executable and checksum, but Windows Application Control blocked launch of the unsigned artifact. Packaged GUI smoke therefore remains **failed/not complete** until the artifact is signed or tested under an approved release policy.
