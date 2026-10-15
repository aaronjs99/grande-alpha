# Development and release

Use Python 3.11 or 3.12 on Windows. `grande.ps1 setup` installs from the checkout with the
selected system Python; a persistent virtual environment is optional. The package source remains
under `scripts/`. Avoid installing development/build outputs into the tracked tree. Before a
refactor, inspect the working tree and preserve other contributors' changes.

## Repository checks

From the repository root:

```powershell
.\grande.ps1 verify
```

This runs lint, compilation, offline acceptance, and a fresh source-distribution/wheel build.
For acceptance checks alone, run `python tools/verify.py`. Its single explicit checklist is
shared by local verification, both supported Python versions in CI, and the isolated Windows
builder. A missing validator or failing suite stops the run; packaging cannot silently use a
smaller checklist. Register new validators in that file once.

The checks use disposable databases, fake broker/model services, rendered Qt widgets and
fixture-authenticated local channels. They cover the desktop and CLI journeys, strict research
inputs, exact candidate review and approval, stop/restart races, paper history, strategy
comparison, account decoding, full activity details, and optional AI admission/cancellation.
They never read a real profile, connect a broker, submit an order, or load a model.

Keep provider-observed execution and recovery, clean-profile installation, prolonged operation,
and generated model accuracy separate from these results. Consult the exact commit's CI run
for current outcomes; earlier passing counts are not a current acceptance certificate.

## Packaging and distribution

Build a clean wheel outside the checkout, install it in a disposable location, and exercise the
headless command line without Qt. Then build and inspect the Windows desktop artifact with its
optional dependencies. The wheel must not include credentials, account data, generated `egg-info`,
local databases, or research datasets. A release also needs current documentation
links, a dependency and secret audit, and a check that all command examples still parse.

The Windows builder stages `scripts/` as a temporary `grande_alpha` package because PyInstaller
does not resolve the editable package mapping by itself. It creates a short-lived packaging
environment using only the declared desktop/runtime dependencies and packaging tools, runs the
offline worker checks there, and audits metadata in the frozen tree. A failed install, acceptance
check, or dependency audit fails the build. Short temporary paths accommodate Windows' default
path-length limit. The resolved versions are saved to
`artifacts/pyinstaller/resolved-requirements.txt`; shared Python packages are not upgraded.
The runtime's cryptography minimum excludes the older dependency version flagged during this
package audit. Source installations need their normal `setup` dependency update; building an
isolated executable does not repair a shared Python installation.
Check both the frozen `--version` and
`--worker --help` entrypoints; a successful executable build alone does not prove the worker was
bundled.

Version information comes from `scripts/_version.py` through package metadata. A version bump is
not proof of production readiness. The 0.19.0 source release retires the separate attended and
live-shadow routes; installed and provider-observed acceptance remain separate work.
`1.0.0` requires a stable public contract, provider-observed execution
and recovery results, signed/distributed artifacts, and support/compliance decisions. It does
not certify profitability.

For `1.0.0`, the exact release candidate must also satisfy these product gates:

1. One coherent, accessible user journey with tested narrow and wide layouts, keyboard/focus
   behavior, and understandable recovery messages.
2. Reproducible research inputs with documented rights, point-in-time provenance, costs, and
   independent review of any performance claim.
3. Provider-observed order, partial-fill, timeout, ambiguous-submission, restart, and stop
   behavior for every supported live route, followed by an independent execution-safety review.
4. Tested install, upgrade, backup, repair, export, and uninstall behavior on clean supported
   Windows profiles, including long-running resource and disconnected operation.
5. Signed and timestamped distribution artifacts tied to a clean commit, reviewed dependencies,
   a vulnerability-response owner, and a practiced release-revocation path.
6. Documented provider permission and market-data rights for the public distribution model,
   plus qualified review of applicable consumer, privacy, financial-promotion, and tax-record
   obligations.
7. A written compatibility and deprecation policy for CLI, configuration, database, receipts,
   diagnostics, and integration contracts.

These are distribution-quality thresholds, not a profitability certificate or guarantee of
uninterrupted broker service. Source checks alone do not establish that any threshold is met.

Historical replay, paper simulation, provider-observed trading, and public deployment are
different evidence levels. Report them separately. Do not claim a live order route is safe or
profitable from a source build alone. Do not place a live order as a release exercise without the
operator's exact approval and accepted financial limits.

## Contribution workflow

Work on `master` for this checkout without rewriting collaborator history. Review an external PR
against the current package layout before incorporating it; a PR based on an older tree may need
an explicit port. Commit only reviewed source and documentation, inspect the staged diff, then push
after the available repository checks. Keep dated research results under [historical records](historical/README.md)
when superseded rather than silently repurposing their evidence.
