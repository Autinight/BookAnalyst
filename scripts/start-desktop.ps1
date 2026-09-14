param([ValidateRange(1, 65535)][int]$Port = 8766)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Test-BookAnalystReady([int]$CandidatePort) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$CandidatePort/"
        return ($response.StatusCode -eq 200 -and $response.Content -match '<title>BookAnalyst</title>')
    } catch {
        return $false
    }
}

try {
    # Reuse an existing instance. If another application owns the port, try the next one.
    while ($true) {
        if (Test-BookAnalystReady $Port) {
            Start-Process "http://127.0.0.1:$Port/"
            exit 0
        }
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        if (-not ($listeners | Where-Object { $_.Port -eq $Port })) { break }
        if ($Port -eq 65535) { throw 'No available port was found.' }
        $Port++
    }

    $logDirectory = Join-Path $projectRoot 'tmp'
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    $launchStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $outputLog = Join-Path $logDirectory "bookanalyst-$Port-$launchStamp.out.log"
    $errorLog = Join-Path $logDirectory "bookanalyst-$Port-$launchStamp.err.log"

    Write-Host "Starting BookAnalyst on port $Port..."
    $serverProcess = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\start.ps1','-Port',"$Port" `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -PassThru

    $deadline = (Get-Date).AddSeconds(60)
    do {
        if (Test-BookAnalystReady $Port) {
            Write-Host "Ready: http://127.0.0.1:$Port/"
            Write-Host "BookAnalyst will keep running in the background."
            Start-Process "http://127.0.0.1:$Port/"
            exit 0
        }
        if ($serverProcess.HasExited) {
            if (Test-Path -LiteralPath $errorLog) {
                Get-Content -LiteralPath $errorLog -Tail 25 | Write-Host
            }
            throw "BookAnalyst exited before it was ready. Log: $errorLog"
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "Startup has not completed after 60 seconds. Check: $outputLog and $errorLog"
} catch {
    Write-Host "BookAnalyst could not be opened: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
