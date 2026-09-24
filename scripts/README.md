# Product source and repository tools

This directory is the installable `grande_alpha` Python package. `domain/`
holds shared types and trading rules; `execution/` owns authorization, orders,
reconciliation, and the local worker. `broker/` and `data/` provide external
inputs, `persistence/` owns durable records, `research/` holds replay and
analysis, and `ui/` contains the desktop interface. `assets/` is packaged with
the app. `windows/` contains developer tooling and is not installed.

The root `grande.ps1` command handles setup, launch, verification, and
packaging. It calls the installed package or a focused Windows tool; it does
not duplicate application logic.
