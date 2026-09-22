param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationPath
)

$ErrorActionPreference = 'Stop'
$ApplicationPath = [IO.Path]::GetFullPath($ApplicationPath)
$HotfixRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $ApplicationPath '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $ApplicationPath 'app.py'))) {
    throw "app.py was not found in $ApplicationPath"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "The existing virtual environment was not found in $ApplicationPath"
}

$files = @(
    'settings_routes.py',
    'training_export_routes.py',
    'templates\inspection_quality.html',
    'templates\admin.html',
    'templates\users.html',
    'templates\settings.html',
    'templates\_admin_nav_icons.html'
)
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupRoot = Join-Path $ApplicationPath ".upgrade_backups\inspection_quality_hotfix_$stamp"
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

foreach ($relative in $files) {
    $source = Join-Path $HotfixRoot $relative
    $destination = Join-Path $ApplicationPath $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Hotfix file is missing: $relative"
    }
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        $backup = Join-Path $backupRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
        Copy-Item -LiteralPath $destination -Destination $backup -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

Push-Location $ApplicationPath
try {
    & $python -m py_compile settings_routes.py training_export_routes.py
    if ($LASTEXITCODE -ne 0) { throw 'Python validation failed.' }
    & $python -c "from app import app; rules={r.rule for r in app.url_map.iter_rules()}; assert '/inspection-quality' in rules and '/api/inspection-quality' in rules; print('Inspection Quality routes: OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Application route validation failed.' }
} finally {
    Pop-Location
}

Write-Host 'Inspection Quality hotfix installed successfully.' -ForegroundColor Green
Write-Host "Backup: $backupRoot"
Write-Host 'Database, .env, uploads and migrations were not changed.'
