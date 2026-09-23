# Product source and repository tools

This directory is the installable `grande_alpha` Python package. Its `broker/`,
`ui/`, and `assets/` directories belong to the package; `windows/` contains
developer tooling and is not included in the installed package.

The root `grande.ps1` command handles setup, launch, verification, and
packaging. It calls the installed package or a focused Windows tool; it does
not duplicate application logic.
