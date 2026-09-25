# Development and release

Use Python 3.11 or 3.12 on Windows. `grande.ps1 setup` installs from the checkout with the
selected system Python; a persistent virtual environment is optional. The package source remains
under `scripts/`. Avoid installing development/build outputs into the tracked tree. Before a
refactor, inspect the working tree and preserve other contributors' changes.

## Repository checks

From the repository root:

```powershell
python -m ruff check scripts
python -m compileall -q scripts
.\grande.ps1 verify
```

The automated regression suite is intentionally not included. These repository checks cover
linting, bytecode compilation, and packaging only; they do not exercise broker behavior,
execution recovery, or interface operation.

For the 0.19.0 integration, also verify the current-user worker's ownership and authentication,
stop fencing, stalled/ambiguous operations, restart recovery, and explicit offline execution-store
upgrade against disposable databases. Confirm that desktop and CLI agree on the same session without
silently widening a reviewed scope. Separately exercise the research MCP with a real local stdio
client: default-off access, fresh session on each opt-in, bounded queue, prompt snapshot,
revocation/timeout, no account fields, and no broker-write tool. Local interface checks are not
provider-observed execution or installed-product acceptance. See the [architecture boundaries](ARCHITECTURE.md) and
[research MCP guide](RESEARCH.md#agent-research-prompts-and-local-mcp).

## Packaging and distribution

Build a clean wheel outside the checkout, install it in a disposable location, and exercise the
headless command line without Qt. Then build and inspect the Windows desktop artifact with its
optional dependencies. The wheel must not include credentials, account data, generated `egg-info`,
local databases, or research datasets. A release also needs current documentation
links, a dependency and secret audit, and a check that all command examples still parse.

The Windows builder stages `scripts/` as a temporary `grande_alpha` package because PyInstaller
does not resolve the editable package mapping by itself. Check both the frozen `--version` and
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

Historical replay, forward shadow observation, provider-observed trading, and public deployment are
different evidence levels. Report them separately. Do not claim a live order route is safe or
profitable from a source build alone. Do not place a live order as a release exercise without the
operator's exact approval and accepted financial limits.

## Contribution workflow

Work on `master` for this checkout without rewriting collaborator history. Review an external PR
against the current package layout before incorporating it; a PR based on an older tree may need
an explicit port. Commit only reviewed source and documentation, inspect the staged diff, then push
after the available repository checks. Keep dated research results under [historical records](historical/README.md)
when superseded rather than silently repurposing their evidence.
