$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
}

& $PythonExe -m pip install --upgrade pip setuptools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m pip uninstall -y momentum-trader
& $PythonExe -m pip install -e "${ProjectRoot}[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Setup complete.' -ForegroundColor Green
