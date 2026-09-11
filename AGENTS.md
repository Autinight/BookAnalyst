# BookAnalyst Agent Notes

## Start The Local Service

Run commands from the project root.

For a foreground development server:

```powershell
powershell -File scripts/start.ps1 -Port 8766
```

Open `http://127.0.0.1:8766`.

The startup script uses `.venv\Scripts\python.exe` by default. If dependencies
are missing, prepare the environment first:

```powershell
uv sync --frozen --extra dev
```

For an agent-managed background server on Windows, use a hidden PowerShell
process and redirect logs:

```powershell
Start-Process -FilePath "powershell.exe" `
  -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\start.ps1','-Port','8766' `
  -WorkingDirectory (Get-Location) `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'tmp\bookanalyst-8766.out.log' `
  -RedirectStandardError 'tmp\bookanalyst-8766.err.log'
```

Verify the service after startup:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8766/ |
  Select-Object StatusCode,StatusDescription
```

Expected result: `200 OK`. Startup diagnostics are written to:

- `tmp/bookanalyst-8766.out.log`
- `tmp/bookanalyst-8766.err.log`

If port `8766` is already occupied, start the same script on another port and
use that port in the URL and log file names.
