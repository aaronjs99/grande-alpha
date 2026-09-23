# Product source and repository tools

This directory contains both the installable `grande_alpha/` package and
Windows-only developer tooling under `windows/`. Packaging finds the Python
package here; `windows/` is not shipped as importable product code.

The root `grande.ps1` command handles setup, launch, verification, and
packaging. It calls the installed package or a focused Windows tool; it does
not duplicate application logic.
