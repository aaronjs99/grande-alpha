# Windows installation and signing boundary

## Working local installation

From the repository or Windows source bundle:

```powershell
.\setup.ps1
.\doctor.ps1 -Full
.\install-local.ps1
.\run.ps1
```

The source launcher uses the machine's signed Python executable and keeps the app code visible and
auditable. `doctor.ps1` reports the source environment, Python signature, optional Robinhood OAuth
state, and packaged-candidate signature without printing credentials or account data.

If the checkout already has `.venv`, the scripts use it. Otherwise, setup creates the managed runtime
at `%LOCALAPPDATA%\GRANDEAlpha\runtime`. Keeping the runtime outside the extracted source tree avoids
the Windows path-length failure caused by deeply nested PySide6 QML files. For isolated automation,
set `GRANDE_ALPHA_RUNTIME_DIR` to another short absolute directory before running setup.

`install-local.ps1` creates Desktop and Start Menu shortcuts that launch the source application via
the trusted system PowerShell and signed Python runtime. The shortcuts point to the current source
folder, so keep that folder in its installed location.

## Why the unsigned executable may not start

PyInstaller can build a technically valid executable without establishing publisher identity.
Windows Smart App Control and enterprise Code Integrity can require an enterprise-trusted signature
and block that file. Renaming, re-zipping, self-signing, disabling Smart App Control, or bypassing
policy is not an acceptable product fix.

`release.ps1` therefore creates two artifacts:

- `grande-alpha-<version>-windows-source.zip`: supported preview path; run setup and doctor.
- `grande-alpha-<version>-unsigned-windows-x64.zip`: signing candidate only; contains an explicit
  `UNSIGNED_BUILD.txt` warning.

## Public binary gate

Before presenting `GRANDEAlpha.exe` as public-ready:

1. Obtain a code-signing identity accepted by the intended Windows policy and legally owned by the
   publisher.
2. Sign the executable and relevant installer with SHA-256 and a trusted timestamp service.
3. Verify `Get-AuthenticodeSignature` reports `Valid` after download on a clean Windows profile.
4. Run onboarding, sandbox, settings, OAuth revocation, stop/cancel, upgrade, uninstall, malware,
   accessibility, and crash-recovery tests against the exact signed hash.
5. Publish the signed checksum, SBOM, version, and support/security contacts.

GitHub Actions currently names its binary artifact `GRANDEAlpha-unsigned-windows-x64`; CI success
proves the code builds, not that Windows trusts the publisher.
