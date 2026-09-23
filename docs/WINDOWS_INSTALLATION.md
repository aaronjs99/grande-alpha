# Windows installation and signing boundary

## Working local installation

From the repository or Windows source bundle:

```powershell
.\grande.ps1 install
.\grande.ps1 run
```

For a source-only run without shortcuts, use `.\grande.ps1 setup` followed by
`.\grande.ps1 run`. The `install` task includes setup. Run `.\grande.ps1 doctor -Full`
after installation to verify the source environment and tests.

The source launcher uses the machine's signed Python executable and keeps the app code visible and
auditable. `.\grande.ps1 doctor` reports the source environment, Python signature, optional Robinhood OAuth
state, and packaged-candidate signature without printing credentials or account data.

If the checkout already has `.venv`, the scripts use it. Otherwise, setup creates the managed runtime
at `%LOCALAPPDATA%\GRANDEAlpha\runtime`. Keeping the runtime outside the extracted source tree avoids
the Windows path-length failure caused by deeply nested PySide6 QML files. For isolated automation,
set `GRANDE_ALPHA_RUNTIME_DIR` to another short absolute directory before running setup.

Normal startup does not discover or merge data from a legacy application directory. If an existing
configuration needs a schema upgrade, run `grande-alpha-cli config upgrade`; it keeps a timestamped
backup beside the selected configuration before writing the upgraded copy. If legacy audit records
need recovery, explicitly name their source directory with
`grande-alpha-cli config import-legacy --source <directory>`; the operation only copies recognized
files and preserves the source directory.

`.\grande.ps1 install` creates Desktop and Start Menu shortcuts that launch the source application via
the trusted system PowerShell and signed Python runtime. The shortcuts point to the current source
folder, so keep that folder in its installed location. Version 0.17 and later do not install a
morning-check shortcut or any Windows scheduled task.

No background scheduler is installed or supported. Live authority is never stored in Task Scheduler,
configuration, Credential Manager, or the receipt database. Starting either bounded real-order path
requires an interactive action in the running desktop application.

### Current OAuth recovery

If a future Robinhood check reports that its cached OAuth token was revoked, first lock local
authority. Credential forgetting requires a clean disconnected state and never cancels an order. If
GRANDE-owned open or unresolved state remains, complete the explicit **STOP + CANCEL** preview,
confirmation, and terminal verification before disconnecting. Then use **Broker → Forget Stored
OAuth Credentials…**, confirm removal, reconnect through the Robinhood browser flow, and rerun the
read-only broker diagnostic. Do not paste tokens into project files or try to bypass OAuth. Forgetting
the local credential does not itself revoke Robinhood-side access; review Robinhood's
[third-party connection guidance](https://robinhood.com/us/en/support/articles/third-party-connections/)
for provider-side connection management.

## Why the unsigned executable may not start

PyInstaller can build a technically valid executable without establishing publisher identity.
Windows Smart App Control and enterprise Code Integrity can require an enterprise-trusted signature
and block that file. Renaming, re-zipping, self-signing, disabling Smart App Control, or bypassing
policy is not an acceptable product fix.

`.\grande.ps1 release` therefore creates two artifacts:

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
