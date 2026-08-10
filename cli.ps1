$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot 'runtime-path.ps1')
$PythonExe = Get-GrandeAlphaPython $ProjectRoot

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Run setup.ps1 first.'
}

& $PythonExe -m grande_alpha.cli @args
exit $LASTEXITCODE
