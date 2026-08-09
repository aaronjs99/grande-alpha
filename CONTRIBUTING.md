# Contributing

Contributions are welcome after a public repository and issue tracker are selected.

## Development

Use Python 3.11 or newer on Windows:

```powershell
.\setup.ps1
.\verify.ps1
```

Keep research mode usable without a broker. New external connections must be disclosed, disabled by default, separately revocable, and covered by tests. No test may place a real order. Do not commit credentials, account data, tax records, local databases, diagnostics, or licensed market data.

Pull requests should include the user-visible risk change, tests, documentation, and screenshots for UI changes. `ruff`, `pytest`, compilation, packaging, and the source/packaged smoke checks must pass.

Unless explicitly marked otherwise, a contribution intentionally submitted for inclusion is licensed under Apache-2.0, consistent with the project license.
