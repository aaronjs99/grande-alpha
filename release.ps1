$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot
$Version = & $PythonExe -c "from grande_alpha import __version__; print(__version__)"
$ReleaseRoot = Join-Path $ProjectRoot "release\grande-alpha-$Version-unsigned-windows-x64"
$Archive = Join-Path $ProjectRoot "release\grande-alpha-$Version-unsigned-windows-x64.zip"
$SourceArchive = Join-Path $ProjectRoot "release\grande-alpha-$Version-windows-source.zip"
$ResolvedProjectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
$ResolvedReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
if (-not $ResolvedReleaseRoot.StartsWith($ResolvedProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing release operation outside project root: $ResolvedReleaseRoot"
}
$GitStatus = @(& git -C $ProjectRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect the release worktree" }
if ($GitStatus.Count -gt 0) {
    throw "Release artifacts must be built from an exact clean commit. Commit or remove every tracked and untracked change first."
}
$ReleaseCommit = (& git -C $ProjectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $ReleaseCommit) { throw "Could not resolve the release commit" }

& (Join-Path $ProjectRoot 'verify.ps1')

$WheelCandidates = @(
    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'artifacts\wheel-check') `
        -Filter "grande_alpha-$Version-*.whl" -File
)
if ($WheelCandidates.Count -ne 1) {
    throw "Expected exactly one verified wheel for version $Version"
}
$RuntimeEnvironment = Join-Path $ProjectRoot "build\release-runtime-$PID"
$BuildEnvironment = Join-Path $ProjectRoot "build\release-builder-$PID"
$RuntimeConstraints = Join-Path $ProjectRoot "build\release-runtime-$PID.txt"
$BuilderAuditRequirements = Join-Path $ProjectRoot "build\release-builder-audit-$PID.txt"
$SetuptoolsRequirement = 'setuptools>=83'
$ResolvedBuildRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build')).TrimEnd('\') + '\'
$ResolvedRuntimeEnvironment = [IO.Path]::GetFullPath($RuntimeEnvironment).TrimEnd('\') + '\'
$ResolvedBuildEnvironment = [IO.Path]::GetFullPath($BuildEnvironment).TrimEnd('\') + '\'
$ResolvedRuntimeConstraints = [IO.Path]::GetFullPath($RuntimeConstraints)
$ResolvedBuilderAuditRequirements = [IO.Path]::GetFullPath($BuilderAuditRequirements)
if (
    -not $ResolvedRuntimeEnvironment.StartsWith($ResolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $ResolvedBuildEnvironment.StartsWith($ResolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $ResolvedRuntimeConstraints.StartsWith($ResolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $ResolvedBuilderAuditRequirements.StartsWith($ResolvedBuildRoot, [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Refusing release environment operation outside the build directory"
}
foreach ($EnvironmentPath in @($RuntimeEnvironment, $BuildEnvironment)) {
    if (Test-Path -LiteralPath $EnvironmentPath) {
        Remove-Item -LiteralPath $EnvironmentPath -Recurse -Force
    }
}
try {
    & $PythonExe -m venv $RuntimeEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Could not create the clean release runtime environment" }
    $RuntimePython = Join-Path $RuntimeEnvironment 'Scripts\python.exe'
    & $RuntimePython -m pip install --disable-pip-version-check --no-input --upgrade `
        $SetuptoolsRequirement
    if ($LASTEXITCODE -ne 0) { throw "Could not install modern setuptools in the release runtime" }
    & $RuntimePython -m pip install --disable-pip-version-check --no-input $WheelCandidates[0].FullName
    if ($LASTEXITCODE -ne 0) { throw "Could not install the verified wheel in the release runtime" }
    $SetuptoolsVersion = (& $RuntimePython -c `
        "from importlib.metadata import version; print(version('setuptools'))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $SetuptoolsVersion) {
        throw "Could not resolve the release runtime setuptools version"
    }

    # Freeze the exact runtime resolution, excluding only the project wheel and packaging bootstrap
    # tools that are not bundled. Setuptools stays pinned because PyInstaller embeds its runtime.
    $RuntimeFreeze = @(& $RuntimePython -m pip freeze --all)
    if ($LASTEXITCODE -ne 0) { throw "Could not freeze the clean release runtime" }
    $PinnedRuntime = @(
        $RuntimeFreeze | Where-Object {
            $_ -notmatch '^(?i:grande[-_]alpha)(?:==|\s*@\s)' -and
            $_ -notmatch '^(?i:(?:pip|wheel))=='
        }
    )
    if ($PinnedRuntime.Count -lt 1) { throw "Clean release runtime produced no dependency closure" }
    $PinnedRuntime | Set-Content -LiteralPath $RuntimeConstraints -Encoding ascii

    & $PythonExe -m venv $BuildEnvironment
    if ($LASTEXITCODE -ne 0) { throw "Could not create the clean release build environment" }
    $BuildPython = Join-Path $BuildEnvironment 'Scripts\python.exe'
    & $BuildPython -m pip install --disable-pip-version-check --no-input `
        --constraint $RuntimeConstraints $SetuptoolsRequirement $WheelCandidates[0].FullName
    if ($LASTEXITCODE -ne 0) { throw "Could not install the verified wheel in the release builder" }
    & $BuildPython -m pip install --disable-pip-version-check --no-input `
        --constraint $RuntimeConstraints 'pyinstaller>=6.13,<7'
    if ($LASTEXITCODE -ne 0) { throw "Could not install the isolated release builder" }

    $RuntimeClosure = @(& $RuntimePython -m pip freeze --all) | Sort-Object
    $BuildClosure = @(& $BuildPython -m pip freeze --all) | Sort-Object
    $MissingOrChangedRuntime = @(
        Compare-Object -ReferenceObject $RuntimeClosure -DifferenceObject $BuildClosure |
            Where-Object { $_.SideIndicator -eq '<=' }
    )
    if ($MissingOrChangedRuntime.Count -gt 0) {
        throw "Release builder does not contain the exact frozen runtime dependency closure"
    }

    $BuilderVersionsJson = & $BuildPython -c `
        "import importlib.metadata as m, json; names = ('pyinstaller', 'pyinstaller-hooks-contrib', 'packaging', 'setuptools'); print(json.dumps({name: m.version(name) for name in names}, sort_keys=True))"
    if ($LASTEXITCODE -ne 0 -or -not $BuilderVersionsJson) {
        throw "Could not resolve exact release builder package versions"
    }
    $BuilderVersions = $BuilderVersionsJson | ConvertFrom-Json
    if (
        -not $BuilderVersions.pyinstaller -or
        -not $BuilderVersions.'pyinstaller-hooks-contrib' -or
        -not $BuilderVersions.packaging -or
        $BuilderVersions.setuptools -ne $SetuptoolsVersion
    ) {
        throw "Release builder provenance is incomplete or does not match the runtime setuptools version"
    }
    $AuditedBuildClosure = @(
        $BuildClosure | Where-Object {
            $_ -notmatch '^(?i:grande[-_]alpha)(?:==|\s*@\s)' -and
            $_ -notmatch '^(?i:(?:pip|wheel))=='
        }
    )
    $AuditedBuildClosure | Set-Content -LiteralPath $BuilderAuditRequirements -Encoding ascii
    & $PythonExe -m pip_audit --strict --progress-spinner off --disable-pip --no-deps `
        --requirement $BuilderAuditRequirements
    if ($LASTEXITCODE -ne 0) { throw "Release builder dependency vulnerability audit failed" }

    & (Join-Path $ProjectRoot 'build.ps1') -PythonExecutable $BuildPython

    if (Test-Path -LiteralPath $ReleaseRoot) {
        Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
    Copy-Item -Path (Join-Path $ProjectRoot 'dist\GRANDEAlpha\*') -Destination $ReleaseRoot -Recurse
    $PackagedPythonRoot = Join-Path $ReleaseRoot '_internal'
    if (-not (Test-Path -LiteralPath $PackagedPythonRoot -PathType Container)) {
        throw "Packaged Python dependency directory is missing"
    }
    & $PythonExe -m pip_audit --strict --progress-spinner off --path $PackagedPythonRoot
    if ($LASTEXITCODE -ne 0) { throw "Packaged dependency vulnerability audit failed" }
    @"
UNSIGNED BUILD PROVENANCE

Release commit: $ReleaseCommit
PyInstaller==$($BuilderVersions.pyinstaller)
pyinstaller-hooks-contrib==$($BuilderVersions.'pyinstaller-hooks-contrib')
packaging==$($BuilderVersions.packaging)
setuptools==$($BuilderVersions.setuptools)

These exact builder packages were vulnerability-audited before packaging. They may contribute
embedded bootloader, runtime-hook, hook, or packaging-support code that the installed-runtime SBOM
does not enumerate. This provenance record is not a file-level inventory or distribution approval.
"@ | Set-Content -LiteralPath (Join-Path $ReleaseRoot 'BUILD-PROVENANCE.txt') -Encoding utf8
    @'
UNSIGNED RELEASE CANDIDATE

This executable has not been Authenticode-signed and may be blocked by Windows Smart App Control
or enterprise Code Integrity policy. It is an internal preview, not a public-installation artifact,
and must not be distributed. Signing alone is insufficient: third-party notice review and clean-
profile verification remain open release gates.

RUNTIME-SBOM.cdx.json records the frozen installed application runtime environment mirrored into
this build, including the setuptools/pkg_resources runtime embedded in the package. The packaged
Python distribution metadata is checked with pip-audit before archiving. BUILD-PROVENANCE.txt
separately records the exact PyInstaller, hooks-contrib, packaging, and setuptools builder versions.

This runtime SBOM is not an exact inventory of the packaged files. It does not enumerate the
PyInstaller bootloader, runtime hooks, hooks-contrib modules, Windows, the Python interpreter, or
native operating-system components; non-runtime build/test tools are also outside its scope.
'@ | Set-Content -LiteralPath (Join-Path $ReleaseRoot 'UNSIGNED_BUILD.txt') -Encoding utf8

    $SbomPath = Join-Path $ReleaseRoot 'RUNTIME-SBOM.cdx.json'
    & $RuntimePython -m pip uninstall --yes pip wheel
    if ($LASTEXITCODE -ne 0) { throw "Could not remove non-embedded packaging tools from the SBOM runtime" }
    & $PythonExe -m cyclonedx_py environment $RuntimePython `
        --pyproject (Join-Path $ProjectRoot 'pyproject.toml') `
        --mc-type application --output-reproducible --output-format JSON --output-file $SbomPath
    if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed with exit code $LASTEXITCODE" }
}
finally {
    foreach ($EnvironmentPath in @($RuntimeEnvironment, $BuildEnvironment)) {
        if (Test-Path -LiteralPath $EnvironmentPath) {
            Remove-Item -LiteralPath $EnvironmentPath -Recurse -Force
        }
    }
    foreach ($TemporaryFile in @($RuntimeConstraints, $BuilderAuditRequirements)) {
        if (Test-Path -LiteralPath $TemporaryFile) {
            Remove-Item -LiteralPath $TemporaryFile -Force
        }
    }
}
$SbomPath = Join-Path $ReleaseRoot 'RUNTIME-SBOM.cdx.json'
$SbomText = Get-Content -Raw -LiteralPath $SbomPath
if ($SbomText -match '(?i)file:/{2,3}|[A-Za-z]:\\Users\\') {
    throw "Refusing to publish an SBOM containing a local workstation path"
}
$Sbom = $SbomText | ConvertFrom-Json
$OwnComponents = @(
    @($Sbom.metadata.component) + @($Sbom.components) |
        Where-Object { $_.name -eq 'grande-alpha' }
)
$SetuptoolsComponents = @(
    @($Sbom.components) |
        Where-Object { $_.name -eq 'setuptools' }
)
$DevelopmentComponents = @(
    @($Sbom.components) |
        Where-Object {
            $_.name -in @(
                'build', 'cyclonedx-bom', 'pip', 'pyinstaller', 'pytest', 'ruff', 'wheel'
            )
        }
)
if (
    $OwnComponents.Count -ne 1 -or
    $OwnComponents[0].version -ne $Version -or
    $OwnComponents[0].type -ne 'application' -or
    $SetuptoolsComponents.Count -ne 1 -or
    $SetuptoolsComponents[0].version -ne $SetuptoolsVersion -or
    $DevelopmentComponents.Count -ne 0
) {
    throw "Runtime SBOM must contain one grande-alpha $Version application, the embedded setuptools $SetuptoolsVersion runtime, and no development-only components"
}

if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -LiteralPath $ReleaseRoot -DestinationPath $Archive -CompressionLevel Optimal
$Hash = Get-FileHash -LiteralPath $Archive -Algorithm SHA256
"$($Hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($Archive))" |
    Set-Content -LiteralPath "$Archive.sha256" -Encoding ascii
Write-Host "Release bundle: $Archive" -ForegroundColor Green
Write-Host "SHA-256: $($Hash.Hash.ToLowerInvariant())" -ForegroundColor Green

if (Test-Path -LiteralPath $SourceArchive) {
    Remove-Item -LiteralPath $SourceArchive -Force
}
# Archive only files tracked by the exact release commit. This structurally excludes local caches,
# bytecode, egg-info, build products, secrets, and any other ignored or untracked workstation files.
$SourcePrefix = "grande-alpha-$Version-windows-source/"
& git -C $ProjectRoot archive --format=zip --prefix=$SourcePrefix --output=$SourceArchive $ReleaseCommit
if ($LASTEXITCODE -ne 0) { throw "Source archive generation failed with exit code $LASTEXITCODE" }
$SourceHash = Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256
"$($SourceHash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($SourceArchive))" |
    Set-Content -LiteralPath "$SourceArchive.sha256" -Encoding ascii
Write-Host "Source bundle: $SourceArchive" -ForegroundColor Green
Write-Host "SHA-256: $($SourceHash.Hash.ToLowerInvariant())" -ForegroundColor Green
Write-Host "Release commit: $ReleaseCommit" -ForegroundColor Green
