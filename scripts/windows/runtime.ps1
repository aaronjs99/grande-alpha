function Get-GrandeAlphaPython([string]$ProjectRoot, [switch]$Windowed) {
    if ($env:GRANDE_ALPHA_PYTHON) {
        if (-not [IO.Path]::IsPathRooted($env:GRANDE_ALPHA_PYTHON)) {
            throw 'GRANDE_ALPHA_PYTHON must be an absolute path.'
        }
        $Python = [IO.Path]::GetFullPath($env:GRANDE_ALPHA_PYTHON)
    } else {
        $Command = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $Command) {
            throw 'Python 3.11 or 3.12 was not found. Install Python or set GRANDE_ALPHA_PYTHON.'
        }
        $Python = $Command.Source
    }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python executable not found: $Python"
    }
    if ($Windowed) {
        $Python = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
            throw "Windowed Python executable not found: $Python"
        }
    }
    return $Python
}
