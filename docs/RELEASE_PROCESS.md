# Release and versioning

This guide combines the release procedure, versioning policy, and exact-artifact checklist.
It does not establish broker or provider approval.

## 1. Select the release commit

- Use a clean commit on the intended release branch.
- Confirm the version follows the [versioning policy](#versioning-policy).
- Update `src/grande_alpha/_version.py`, then confirm the built package, release notes, and proposed tag agree.
- Review all changes since the previous tag, including dependencies, migrations, permissions, data
  flows, provider contracts, and user-visible risk.

## 2. Verify source

On a supported clean Windows environment:

```powershell
.\grande.ps1 setup
.\grande.ps1 verify
```

CI must pass on every supported Python version. Dependency-audit findings require remediation or a
documented, time-bounded exception. No release test may place a real order.

## 3. Build candidates

```powershell
.\grande.ps1 release
```

The process creates a source bundle and an explicitly unsigned Windows binary candidate, together
with checksums and an SBOM. Inspect archive contents for credentials, local data, caches, diagnostics,
account details, licensed datasets, and development-only files.

## 4. Sign and verify

Public binary distribution requires a trusted publisher identity. Sign the executable and installer
with SHA-256 and a trusted timestamp. Verify the downloaded artifact reports a valid signature on a
clean Windows profile. Never solve a trust failure by weakening Windows security policy.

## 5. Acceptance test the exact artifact

Exercise onboarding, offline research, CSV import, evidence review, settings, responsive layouts,
diagnostic export, upgrade, repair, uninstall, and data deletion. Broker-dependent acceptance tests
must use an approved non-production path and must cover consent, revocation, disconnect, ambiguous
acknowledgement, stop/cancel, and recovery without risking a real account.

## 6. Publish

- Create tag `vMAJOR.MINOR.PATCH` from the verified commit.
- Publish release notes with supported systems, installation path, migrations, security changes,
  known limitations, checksums, SBOM, signing identity, and support/security links.
- Publish only artifacts produced from that tag.
- Keep unsigned candidates unmistakably labeled and separate from trusted public downloads.

## 7. Post-release

- Verify links and downloads from a clean profile.
- Monitor security, support, installation, and migration reports.
- Preserve the release artifacts, checksums, SBOM, notes, and source tag.
- If a release must be withdrawn, publish the reason, affected hashes, operator action, and replacement
  status without concealing safety-relevant information.

## Versioning policy

GRANDE Alpha uses [Semantic Versioning](https://semver.org/) in the form `MAJOR.MINOR.PATCH`.

### Before 1.0.0

While the major version is `0`, the product is a community preview. Minor releases may change public
configuration, storage, CLI, evidence, or integration contracts when necessary to reach the stable
product contract. The project still treats persisted user data seriously: migrations must be tested,
documented, and fail safely.

- `0.MINOR.0` introduces a coherent feature or contract revision.
- `0.MINOR.PATCH` fixes defects without intentionally broadening product scope.

### Version 1.0.0

Version 1.0.0 establishes the first stable public contract. It is released only after every mandatory
gate in the [roadmap to 1.0.0](ROADMAP_TO_1_0.md) and [public release checklist](#public-release-checklist)
passes for the exact release candidate.

After 1.0.0:

- `MAJOR` changes may break documented public compatibility.
- `MINOR` changes add backward-compatible capability.
- `PATCH` changes provide backward-compatible fixes.

Strategy research results, evidence-policy versions, database schemas, and broker contracts retain
their own explicit version identifiers. The package version does not silently substitute for those
domain-specific versions.

### Release identifiers

Git tags use `vMAJOR.MINOR.PATCH`. The package, application About dialog, source bundle, candidate
archive, checksums, SBOM, and release notes must report the same version. Pre-releases may use SemVer
suffixes such as `-rc.1` when the packaging pipeline supports them consistently.

### Compatibility and deprecation

A release that changes persisted or public behavior must:

1. describe the change and migration in `CHANGELOG.md`;
2. preserve or explicitly migrate supported local data;
3. reject unsupported state with a recovery-oriented error rather than guessing;
4. provide a deprecation period after 1.0.0 unless an immediate safety or security fix is required;
5. keep old evidence and receipts identifiable even when they are no longer eligible for promotion.

Security fixes may tighten behavior without a normal deprecation period when continued compatibility
would preserve an unsafe path. Such changes must be prominent in the release notes.

## Public release checklist

A public release is ready only when every required item is checked for that exact commit and artifact.
For version 1.0.0, every mandatory item in the [roadmap to 1.0.0](ROADMAP_TO_1_0.md) must also pass.

### Product and safety

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

### Engineering

- [ ] `.\grande.ps1 verify` passes.
- [ ] Dependency vulnerability audit passes or exceptions are documented.
- [ ] Source and packaged GUI smoke tests pass.
- [ ] SBOM and SHA-256 checksums are generated.
- [ ] Release archive is built from a clean commit and scanned.
- [ ] Package, application, tag, artifacts, checksums, SBOM, and release notes report one version.
- [ ] Windows executable is signed with a trusted code-signing certificate.
- [ ] Restore, migration, and uninstall/data-deletion paths are tested.

### Community and legal

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
