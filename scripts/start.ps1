param([ValidateRange(1, 65535)][int]$Port = 8765, [string]$PythonExecutable = "")
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonExecutable) { $PythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'Run uv sync --frozen --extra dev in the project directory first.'
}
# Include persisted PATH updates when launched by an older desktop process.
$pathEntries = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$combinedPath = @($env:Path, [Environment]::GetEnvironmentVariable('Path', 'Machine'),
    [Environment]::GetEnvironmentVariable('Path', 'User')) -join ';'
$env:Path = (($combinedPath -split ';') | Where-Object { $_ -and $pathEntries.Add($_) }) -join ';'
if (-not (Get-Command xelatex -ErrorAction SilentlyContinue)) {
    Write-Warning 'XeLaTeX is not on PATH; TeX compilation will stop at S7 until it is available.'
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
& $pythonExecutable -m bookanalyst --workspace $projectRoot --port $Port
exit $LASTEXITCODE
