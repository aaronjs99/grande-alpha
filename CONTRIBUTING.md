# Contributing to GRANDE Alpha

Thank you for helping improve GRANDE Alpha. Contributions are welcome through
[issues](https://github.com/aaronjs99/grande-alpha/issues) and pull requests.

## Before you begin

- Search existing issues and pull requests before starting duplicate work.
- Use a public issue for reproducible bugs and product proposals.
- Use the [private security process](SECURITY.md) for vulnerabilities.
- Never post credentials, OAuth material, account identifiers, balances, positions, order details,
  broker receipts, tax records, licensed market data, or unreviewed diagnostics.
- Keep research mode fully usable without a broker.

## Development setup

GRANDE Alpha supports 64-bit Windows with Python 3.11 or 3.12.

```powershell
git clone https://github.com/aaronjs99/grande-alpha.git
cd grande-alpha
.\grande.ps1 setup
.\grande.ps1 verify
```

Use these wrappers for local source work: they remove generated `grande_alpha.egg-info` metadata
after installation and package verification. Direct setuptools commands may leave it behind.

Run the source application only when interactive UI testing is necessary:

```powershell
.\grande.ps1 run
```

## Engineering expectations

- Preserve the separation between research, broker reads, shadow observation, supervised orders,
  and autonomous authority.
- Default every new external connection or sensitive capability to disabled and make it separately
  revocable.
- Fail closed when account, quote, order, evidence, session, or provider state is missing or unknown.
- Never add an example or screenshot that connects to a live broker or places an order.
- Update user-facing documentation and the changelog when behavior or risk changes.
- Keep generated files, local databases, credentials, diagnostics, licensed data, and build artifacts
  out of Git.

## Verify your change

The standard check runs linting, bytecode compilation, and a wheel build:

```powershell
.\grande.ps1 verify
```

For UI changes, also exercise the supported portrait and landscape layouts and attach only redacted
screenshots. For packaging changes, run `.\grande.ps1 build` and treat the result as an unsigned candidate.

## Pull requests

Keep each pull request focused. Explain:

1. the user problem and proposed behavior;
2. any change to safety, privacy, evidence, or broker authority;
3. the checks and manual review performed; and
4. documentation or migration effects.

The pull-request template contains the required checklist. A passing CI run proves only that the
repository checks completed; it does not establish trading profitability, broker approval, legal
suitability, or release readiness.

Changes intended for the stable product should identify which
[1.0.0 roadmap gate](docs/ROADMAP_TO_1_0.md) they advance and must preserve the
[product contract](docs/PRODUCT_CONTRACT.md).

Unless explicitly stated otherwise, contributions intentionally submitted for inclusion are
licensed under the repository's [Apache License 2.0](LICENSE).
