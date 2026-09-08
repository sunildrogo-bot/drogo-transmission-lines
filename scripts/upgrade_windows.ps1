param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseZip,

    [Parameter(Mandatory = $false)]
    [string]$ApplicationPath = (Split-Path -Parent $PSScriptRoot),

    [switch]$SkipDependencyInstall,
    [switch]$SkipMigration
)

$ErrorActionPreference = 'Stop'

function Assert-PathInsideApplication([string]$PathToCheck) {
    $appFull = [IO.Path]::GetFullPath($ApplicationPath).TrimEnd('\') + '\'
    $candidate = [IO.Path]::GetFullPath($PathToCheck)
    if (-not $candidate.StartsWith($appFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe path outside application: $candidate"
    }
}

$ApplicationPath = [IO.Path]::GetFullPath($ApplicationPath)
$ReleaseZip = [IO.Path]::GetFullPath($ReleaseZip)
if (-not (Test-Path -LiteralPath "$ApplicationPath\app.py" -PathType Leaf)) {
    throw "app.py was not found in application path: $ApplicationPath"
}
if (-not (Test-Path -LiteralPath $ReleaseZip -PathType Leaf)) {
    throw "Release ZIP was not found: $ReleaseZip"
}

$python = "$ApplicationPath\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The existing virtual environment was not found: $python"
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$upgradeRoot = "$ApplicationPath\.upgrade_backups"
$extractRoot = "$upgradeRoot\incoming_$stamp"
$rollbackZip = "$upgradeRoot\source_before_$stamp.zip"
$rollbackTemp = Join-Path ([IO.Path]::GetTempPath()) "drogo_source_before_$stamp.zip"
Assert-PathInsideApplication $upgradeRoot
Assert-PathInsideApplication $extractRoot
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null

try {
    Expand-Archive -LiteralPath $ReleaseZip -DestinationPath $extractRoot -Force
    $incomingApp = "$extractRoot\drogo_trans_pilot"
    $verifier = "$incomingApp\scripts\build_release.py"
    if (-not (Test-Path -LiteralPath "$incomingApp\RELEASE_MANIFEST.json") -or
        -not (Test-Path -LiteralPath $verifier)) {
        throw 'This is not a verified DROGO source-only release.'
    }

    & $python $verifier --verify $ReleaseZip
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }

    # Preserve a source-only rollback package. Runtime data is backed up
    # separately and is never copied into this ZIP.
    # The safe release builder deliberately refuses to write inside its source
    # tree. Build in Windows Temp, verify there, then move the completed ZIP
    # into the application's protected rollback directory.
    & $python $verifier --source $ApplicationPath --output $rollbackTemp --allow-incomplete-source
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the source rollback package.' }
    Move-Item -LiteralPath $rollbackTemp -Destination $rollbackZip -Force

    $database = "$ApplicationPath\instance\nova.db"
    if (Test-Path -LiteralPath $database -PathType Leaf) {
        $databaseBackup = "$ApplicationPath\instance\nova_before_upgrade_$stamp.db"
        Assert-PathInsideApplication $databaseBackup
        Copy-Item -LiteralPath $database -Destination $databaseBackup -Force
        if ((Get-Item $database).Length -ne (Get-Item $databaseBackup).Length) {
            throw 'SQLite pre-upgrade backup size verification failed.'
        }
        Write-Host "SQLite backup: $databaseBackup"
    } else {
        Write-Warning 'No local instance\nova.db was found. Back up PostgreSQL with its server backup tool before continuing.'
    }

    # Copy source only. These locations always belong to the installed app.
    # Robocopy exit codes 0-7 are successful; 8+ indicate an error.
    & robocopy $incomingApp $ApplicationPath /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 `
        /XD "$incomingApp\.venv" "$incomingApp\instance" "$incomingApp\static\uploads" `
            "$incomingApp\.git" "$incomingApp\.upgrade_backups" `
        /XF '.env' '*.db' '*.sqlite' '*.sqlite3' '*.log' '*.tmp' '*.bak' '*.zip'
    if ($LASTEXITCODE -ge 8) { throw "Source copy failed with Robocopy code $LASTEXITCODE." }

    # Remove exact, obsolete source assets from the retired Chimney module.
    # Uploaded inspection data is deliberately excluded from this list.
    $obsoleteSourceFiles = @(
        'static\js\chimney.js',
        'static\js\chimney_viewer.js',
        'static\images\chimney_cover_default.png',
        'migrate_add_defect_columns.py',
        'USE drogo_aerospace;.sql',
        'templates\USE drogo_aerospace;.sql'
    )
    foreach ($relativeFile in $obsoleteSourceFiles) {
        $obsoletePath = Join-Path $ApplicationPath $relativeFile
        Assert-PathInsideApplication $obsoletePath
        if (Test-Path -LiteralPath $obsoletePath -PathType Leaf) {
            Remove-Item -LiteralPath $obsoletePath -Force
            Write-Host "Removed retired source file: $relativeFile"
        }
    }

    if (-not (Test-Path -LiteralPath "$ApplicationPath\.env" -PathType Leaf)) {
        throw '.env is missing after source update. Restore it before starting the application.'
    }
    if (-not (Test-Path -LiteralPath "$ApplicationPath\instance\nova.db" -PathType Leaf)) {
        Write-Warning 'Local nova.db is not present; confirm DATABASE_URL before starting.'
    }

    Push-Location $ApplicationPath
    try {
        if (-not $SkipDependencyInstall) {
            & $python -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        }
        if (-not $SkipMigration) {
            # Avoid an old PowerShell DATABASE_URL overriding this app's .env.
            $oldDatabaseUrl = $env:DATABASE_URL
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
            try {
                & $python -m flask --app app:app db upgrade
                if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
                & $python -m flask --app app:app db current
                if ($LASTEXITCODE -ne 0) { throw 'Database revision verification failed.' }
            } finally {
                if ($null -ne $oldDatabaseUrl) { $env:DATABASE_URL = $oldDatabaseUrl }
            }
        }
    } finally {
        Pop-Location
    }

    Write-Host ''
    Write-Host 'Upgrade completed successfully.' -ForegroundColor Green
    Write-Host "Application preserved: $ApplicationPath"
    Write-Host "Source rollback package: $rollbackZip"
    Write-Host 'The existing .env, .venv, instance data and static\uploads were not replaced.'
} finally {
    if (Test-Path -LiteralPath $rollbackTemp) {
        Remove-Item -LiteralPath $rollbackTemp -Force
    }
    if (Test-Path -LiteralPath $extractRoot) {
        Remove-Item -LiteralPath $extractRoot -Recurse -Force
    }
}
