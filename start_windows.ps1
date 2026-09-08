$ErrorActionPreference = 'Stop'
$application = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $application '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $application '.env') -PathType Leaf)) {
    throw "Application configuration not found: $application\.env"
}

Push-Location $application
$oldDatabaseUrl = $env:DATABASE_URL
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
$worker = $null
try {
    $worker = Start-Process -FilePath $python -ArgumentList 'job_worker.py', '--poll-seconds', '2' `
        -WorkingDirectory $application -PassThru -NoNewWindow
    Write-Host "Persistent worker started (PID $($worker.Id))."
    Write-Host 'Open http://127.0.0.1:5000 after the Flask server starts.'
    Write-Host 'Press Ctrl+C to stop both processes.'
    & $python app.py
} finally {
    if ($worker -and -not $worker.HasExited) {
        Stop-Process -Id $worker.Id -Force -ErrorAction SilentlyContinue
        $worker.WaitForExit()
    }
    if ($null -ne $oldDatabaseUrl) { $env:DATABASE_URL = $oldDatabaseUrl }
    Pop-Location
}
