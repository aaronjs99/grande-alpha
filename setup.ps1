$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$RuntimeRoot = Get-GrandeAlphaRuntimeRoot $ProjectRoot
$PythonExe = Get-GrandeAlphaPython $ProjectRoot

if (-not (Test-Path -LiteralPath $PythonExe)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $RuntimeRoot) -Force | Out-Null
    python -m venv $RuntimeRoot
}

& $PythonExe -m pip install --upgrade pip setuptools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m pip uninstall -y momentum-trader
& $PythonExe -m pip install -e "${ProjectRoot}[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host "Runtime: $RuntimeRoot" -ForegroundColor Green
