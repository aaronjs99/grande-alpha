$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot -Windowed

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Run setup.ps1 first.'
}

Start-Process -FilePath $PythonExe -ArgumentList '-m', 'grande_alpha.app' -WorkingDirectory $ProjectRoot
