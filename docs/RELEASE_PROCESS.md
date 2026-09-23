# Release process

This process turns one reviewed commit into traceable release artifacts. It does not replace the
[public release checklist](PUBLIC_RELEASE_CHECKLIST.md) or establish broker/provider approval.

## 1. Select the release commit

- Use a clean commit on the intended release branch.
- Confirm the version follows [Versioning](VERSIONING.md).
- Update `src/grande_alpha/_version.py`, then confirm the built package, release notes, and proposed tag agree.
- Review all changes since the previous tag, including dependencies, migrations, permissions, data
  flows, provider contracts, and user-visible risk.

## 2. Verify source

On a supported clean Windows environment:

```powershell
.\setup.ps1
.\verify.ps1
```

CI must pass on every supported Python version. Dependency-audit findings require remediation or a
documented, time-bounded exception. No release test may place a real order.

## 3. Build candidates

```powershell
.\release.ps1
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
