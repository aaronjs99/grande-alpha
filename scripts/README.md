# Repository tools

This directory contains repository-only tooling. The installed application lives
in `src/grande_alpha/`; do not place importable product code here.

Root-level PowerShell files are small Windows entry points for setup, launch,
verification, and packaging. They call the installed package and do not
duplicate application logic.
