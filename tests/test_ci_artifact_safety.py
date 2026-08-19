from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ordinary_ci_smoke_build_is_not_uploaded_as_an_unsigned_artifact() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package_job = workflow.split("\n  package:", maxsplit=1)[1]

    assert "run: .\\build.ps1" in package_job
    assert "actions/upload-artifact" not in package_job
    assert "GRANDEAlpha-unsigned-windows-x64" not in package_job
    assert "path: dist/GRANDEAlpha" not in package_job


def test_ordinary_ci_audits_metadata_retained_in_the_frozen_tree() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package_job = workflow.split("\n  package:", maxsplit=1)[1]

    assert "python -m pip_audit --strict --progress-spinner off" in package_job
    assert "--path dist/GRANDEAlpha/_internal" in package_job
