$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Version = & $PythonExe -c "from grande_alpha import __version__; print(__version__)"
$ReleaseRoot = Join-Path $ProjectRoot "release\grande-alpha-$Version-windows-x64"
$Archive = Join-Path $ProjectRoot "release\grande-alpha-$Version-windows-x64.zip"
$ResolvedProjectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
$ResolvedReleaseRoot = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
if (-not $ResolvedReleaseRoot.StartsWith($ResolvedProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing release operation outside project root: $ResolvedReleaseRoot"
}

& (Join-Path $ProjectRoot 'verify.ps1')
& (Join-Path $ProjectRoot 'build.ps1')

if (Test-Path -LiteralPath $ReleaseRoot) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
Copy-Item -Path (Join-Path $ProjectRoot 'dist\GRANDEAlpha\*') -Destination $ReleaseRoot -Recurse

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
