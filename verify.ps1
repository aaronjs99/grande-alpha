$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

& $PythonExe -m ruff check src tests
& $PythonExe -m pytest -q
& $PythonExe -m compileall -q src
Write-Host 'Verification passed.' -ForegroundColor Green

