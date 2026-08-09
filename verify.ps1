$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

& $PythonExe -m ruff check src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m compileall -q src
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m build --wheel --outdir (Join-Path $ProjectRoot 'artifacts\wheel-check')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Verification passed.' -ForegroundColor Green
