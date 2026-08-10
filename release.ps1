$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot
$Version = & $PythonExe -c "from grande_alpha import __version__; print(__version__)"
$ReleaseRoot = Join-Path $ProjectRoot "release\grande-alpha-$Version-unsigned-windows-x64"
$Archive = Join-Path $ProjectRoot "release\grande-alpha-$Version-unsigned-windows-x64.zip"
$SourceRoot = Join-Path $ProjectRoot "release\grande-alpha-$Version-windows-source"
$SourceArchive = Join-Path $ProjectRoot "release\grande-alpha-$Version-windows-source.zip"
$ResolvedProjectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
$ResolvedReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
if (-not $ResolvedReleaseRoot.StartsWith($ResolvedProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing release operation outside project root: $ResolvedReleaseRoot"
}
$ResolvedSourceRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\') + '\'
if (-not $ResolvedSourceRoot.StartsWith($ResolvedProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing source release operation outside project root: $ResolvedSourceRoot"
}

& (Join-Path $ProjectRoot 'verify.ps1')
& (Join-Path $ProjectRoot 'build.ps1')

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot 'dist\GRANDEAlpha\*') -Destination $ReleaseRoot -Recurse
@'
UNSIGNED RELEASE CANDIDATE

This executable has not been Authenticode-signed and may be blocked by Windows Smart App Control
or enterprise Code Integrity policy. It is not the public-installation artifact. Use the source
bundle with setup.ps1/run.ps1, or sign this candidate with a trusted code-signing identity.
'@ | Set-Content -LiteralPath (Join-Path $ReleaseRoot 'UNSIGNED_BUILD.txt') -Encoding utf8

& $PythonExe -m cyclonedx_py environment --output-format JSON --output-file (Join-Path $ReleaseRoot 'SBOM.cdx.json')
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed with exit code $LASTEXITCODE" }

if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -LiteralPath $ReleaseRoot -DestinationPath $Archive -CompressionLevel Optimal
$Hash = Get-FileHash -LiteralPath $Archive -Algorithm SHA256
"$($Hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($Archive))" |
    Set-Content -LiteralPath "$Archive.sha256" -Encoding ascii
Write-Host "Release bundle: $Archive" -ForegroundColor Green
Write-Host "SHA-256: $($Hash.Hash.ToLowerInvariant())" -ForegroundColor Green

if (Test-Path -LiteralPath $SourceRoot) {
    Remove-Item -LiteralPath $SourceRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $SourceRoot -Force | Out-Null
foreach ($Directory in @('src', 'docs', 'assets', 'tests')) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $Directory) -Destination $SourceRoot -Recurse
}
foreach ($File in @(
    'pyproject.toml', 'runtime-path.ps1', 'setup.ps1', 'run.ps1', 'install-local.ps1',
    'doctor.ps1', 'verify.ps1', 'build.ps1',
    'Start GRANDE Alpha.cmd', 'README.md', 'CHANGELOG.md', 'LICENSE', 'NOTICE', 'PRIVACY.md',
    'SECURITY.md', 'SUPPORT.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md'
)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $File) -Destination $SourceRoot
}
if (Test-Path -LiteralPath $SourceArchive) {
    Remove-Item -LiteralPath $SourceArchive -Force
}
Compress-Archive -LiteralPath $SourceRoot -DestinationPath $SourceArchive -CompressionLevel Optimal
$SourceHash = Get-FileHash -LiteralPath $SourceArchive -Algorithm SHA256
"$($SourceHash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($SourceArchive))" |
    Set-Content -LiteralPath "$SourceArchive.sha256" -Encoding ascii
Write-Host "Source bundle: $SourceArchive" -ForegroundColor Green
Write-Host "SHA-256: $($SourceHash.Hash.ToLowerInvariant())" -ForegroundColor Green
