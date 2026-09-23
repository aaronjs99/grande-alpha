# Versioning policy

GRANDE Alpha uses [Semantic Versioning](https://semver.org/) in the form `MAJOR.MINOR.PATCH`.

## Before 1.0.0

While the major version is `0`, the product is a community preview. Minor releases may change public
configuration, storage, CLI, evidence, or integration contracts when necessary to reach the stable
product contract. The project still treats persisted user data seriously: migrations must be tested,
documented, and fail safely.

- `0.MINOR.0` introduces a coherent feature or contract revision.
- `0.MINOR.PATCH` fixes defects without intentionally broadening product scope.

## Version 1.0.0

Version 1.0.0 establishes the first stable public contract. It is released only after every mandatory
gate in the [roadmap to 1.0.0](ROADMAP_TO_1_0.md) and [public release checklist](PUBLIC_RELEASE_CHECKLIST.md)
passes for the exact release candidate.

After 1.0.0:

- `MAJOR` changes may break documented public compatibility.
- `MINOR` changes add backward-compatible capability.
- `PATCH` changes provide backward-compatible fixes.

Strategy research results, evidence-policy versions, database schemas, and broker contracts retain
their own explicit version identifiers. The package version does not silently substitute for those
domain-specific versions.

## Release identifiers

Git tags use `vMAJOR.MINOR.PATCH`. The package, application About dialog, source bundle, candidate
archive, checksums, SBOM, and release notes must report the same version. Pre-releases may use SemVer
suffixes such as `-rc.1` when the packaging pipeline supports them consistently.

## Compatibility and deprecation

A release that changes persisted or public behavior must:

1. describe the change and migration in `CHANGELOG.md`;
2. preserve or explicitly migrate supported local data;
3. reject unsupported state with a recovery-oriented error rather than guessing;
4. provide a deprecation period after 1.0.0 unless an immediate safety or security fix is required;
5. keep old evidence and receipts identifiable even when they are no longer eligible for promotion.

Security fixes may tighten behavior without a normal deprecation period when continued compatibility
would preserve an unsafe path. Such changes must be prominent in the release notes.
