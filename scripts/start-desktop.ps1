param([ValidateRange(1, 65535)][int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show('Run uv sync --frozen --extra desktop --extra dev in the project directory first.', 'BookAnalyst') | Out-Null
    exit 1
}
$pathEntries = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$combinedPath = @($env:Path, [Environment]::GetEnvironmentVariable('Path', 'Machine'),
    [Environment]::GetEnvironmentVariable('Path', 'User')) -join ';'
$env:Path = (($combinedPath -split ';') | Where-Object { $_ -and $pathEntries.Add($_) }) -join ';'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
Start-Process -FilePath $pythonExecutable -ArgumentList '-m','bookanalyst.desktop','--workspace',('"' + $projectRoot + '"'),'--port',$Port -WorkingDirectory $projectRoot -WindowStyle Hidden
