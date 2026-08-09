$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Run setup.ps1 first.'
}

Start-Process -FilePath $PythonExe -ArgumentList '-m', 'momentum_trader.app' -WorkingDirectory $ProjectRoot

