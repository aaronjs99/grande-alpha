param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'verify', 'build', 'release', 'doctor', 'install', 'run', 'cli', 'help')]
    [string]$Task = 'help',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskArgs
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WindowsTools = Join-Path $ProjectRoot 'scripts\windows'
. (Join-Path $WindowsTools 'runtime.ps1')

function Invoke-Python([string[]]$Arguments) {
    $PythonExe = Get-GrandeAlphaPython $ProjectRoot
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Task) {
    'setup' {
        $PythonExe = Get-GrandeAlphaPython $ProjectRoot
        & $PythonExe -m pip install --user -e "${ProjectRoot}[desktop,dev]"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host 'Setup complete.' -ForegroundColor Green
        Write-Host "Python: $PythonExe" -ForegroundColor Green
    }
    'verify' {
        Invoke-Python -Arguments @('-m', 'ruff', 'check', 'scripts', 'tests')
        Invoke-Python -Arguments @('-m', 'compileall', '-q', 'scripts')
        $env:QT_QPA_PLATFORM = 'offscreen'
        Invoke-Python -Arguments @('-m', 'pytest', '-q')
        # Build from a fresh sdist so removed source files cannot leak from build/lib.
        Invoke-Python -Arguments @('-m', 'build', '--outdir', (Join-Path $ProjectRoot 'artifacts\wheel-check'))
        Write-Host 'Verification passed.' -ForegroundColor Green
    }
    'build' { & (Join-Path $WindowsTools 'build.ps1') @TaskArgs; exit $LASTEXITCODE }
    'release' { & (Join-Path $WindowsTools 'release.ps1') @TaskArgs; exit $LASTEXITCODE }
    'doctor' { & (Join-Path $WindowsTools 'doctor.ps1') @TaskArgs; exit $LASTEXITCODE }
    'install' { & (Join-Path $WindowsTools 'install.ps1') @TaskArgs; exit $LASTEXITCODE }
    'run' {
        $PythonExe = Get-GrandeAlphaPython $ProjectRoot
        & $PythonExe -c 'import grande_alpha.app'
        if ($LASTEXITCODE -ne 0) { throw 'App dependencies are missing. Run .\grande.ps1 setup first.' }
        $PythonExe = Get-GrandeAlphaPython $ProjectRoot -Windowed
        Start-Process -FilePath $PythonExe -ArgumentList '-m', 'grande_alpha.app' -WorkingDirectory $ProjectRoot
    }
    'cli' { Invoke-Python -Arguments (@('-m', 'grande_alpha.cli') + $TaskArgs) }
    'help' {
        Write-Host 'Usage: .\grande.ps1 <setup|verify|build|release|doctor|install|run|cli> [arguments]'
        Write-Host 'Use .\grande.ps1 cli --help for product commands.'
    }
}
