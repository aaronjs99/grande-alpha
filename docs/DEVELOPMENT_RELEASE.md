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

This runs lint, compilation and seven local acceptance scenarios in `tools/verify.py`.
There is no pytest suite, coverage service or GitHub Actions workflow. For acceptance alone,
run `python tools/verify.py`. The checklist covers:

1. Offscreen desktop startup, narrow setup and empty journal without starting a worker.
2. Strict JSON inputs, incomplete candidate rejection and source-preserving edits.
3. Read-only records that neither initialize missing databases nor change saved files.
4. Durable Stop, stale-start rejection and stopped state across restart.
5. Broker catalog compatibility and account/portfolio/position decoding using local fixtures.
6. Quiet paper-only exit while uncertain broker context still warns.
7. A synthetic paper session through desktop controls and authenticated local IPC, chart updates,
   complete journal pagination, exact CSV export, interrupted-save protection and Stop.

All data is disposable. These checks never read the operator's profile, connect a broker,
submit an order or load an AI model. Add a regression only when it protects an important
workflow or a consequential failure; avoid rebuilding a large suite of implementation checks.
Provider-observed execution, clean-profile installation, prolonged operation, strategy results
and model accuracy remain separate evidence. Record the exact commit and local command output.

## Packaging and distribution

Build a clean wheel outside the checkout, install it in a disposable location, and exercise the
headless command line without Qt. Then build and inspect the Windows desktop artifact with its
optional dependencies. The wheel must not include credentials, account data, generated `egg-info`,
local databases, or research datasets. A release also needs current documentation
links, a dependency and secret audit, and a check that all command examples still parse.

The Windows builder stages `scripts/` as a temporary `grande_alpha` package because PyInstaller
does not resolve the editable package mapping by itself. It creates a short-lived packaging
environment using only the declared desktop/runtime dependencies and packaging tools. It checks
the frozen desktop, then starts the actual packaged worker with a disposable profile and runs
paper Start → four fills → saved journal/CSV → shutdown → restart without resuming → repeat →
Stop → exit. This uses virtual money and makes no broker or model request. Finally it audits
metadata in the frozen tree. A failed install, acceptance check or dependency audit fails the
build. The source checklist is not repeated inside packaging. Keep the installed app closed
while rebuilding. Short temporary paths accommodate Windows' default
path-length limit. The resolved versions are saved to
`artifacts/pyinstaller/resolved-requirements.txt`; shared Python packages are not upgraded.
The runtime's cryptography minimum excludes the older dependency version flagged during this
package audit. Source installations need their normal `setup` dependency update; building an
isolated executable does not repair a shared Python installation.
Check both the frozen `--version` and
`--worker --help` entrypoints; a successful executable build alone does not prove the worker was
bundled.

To repeat only the packaged paper workflow:

```powershell
python tools/verify.py --executable dist/GRANDEAlpha/GRANDEAlpha.exe
```

The local `grande.ps1 release` command builds its own fresh source distribution and wheel for
the clean release commit before preparing the unsigned archive. Ordinary `verify` does not
build distribution archives. There is no automatic release or publication.

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
