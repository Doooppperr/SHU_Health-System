#requires -Version 7.0

param(
    [string]$Server = "111.229.87.94",
    [string]$SshUser = "ubuntu",
    [switch]$SkipTests,
    [switch]$SyncDemoDatabase,
    [switch]$SyncMailSettings
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$releaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$archiveName = "healthdoc-app-$releaseId.tar.gz"
$archivePath = Join-Path ([System.IO.Path]::GetTempPath()) $archiveName
$remoteArchive = "/home/$SshUser/$archiveName"
$remoteScript = "/home/$SshUser/healthdoc-release-server.sh"
$demoSnapshotName = "healthdoc-demo-$releaseId.db"
$demoSnapshotPath = Join-Path ([System.IO.Path]::GetTempPath()) $demoSnapshotName
$remoteDemoSnapshot = "/home/$SshUser/$demoSnapshotName"
$demoAssetsName = "healthdoc-demo-assets-$releaseId.tar.gz"
$demoAssetsPath = Join-Path ([System.IO.Path]::GetTempPath()) $demoAssetsName
$remoteDemoAssets = "/home/$SshUser/$demoAssetsName"
$mailSettingsName = "healthdoc-mail-$releaseId.env"
$mailSettingsPath = Join-Path ([System.IO.Path]::GetTempPath()) $mailSettingsName
$remoteMailSettings = "/home/$SshUser/$mailSettingsName"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

try {
    if (-not $SkipTests) {
        Push-Location (Join-Path $projectRoot "backend")
        try {
            & ".\.venv\Scripts\python.exe" -m pip check
            Assert-LastExitCode "Backend dependency check"
            & ".\.venv\Scripts\python.exe" -m pytest -q
            Assert-LastExitCode "Backend tests"
        }
        finally {
            Pop-Location
        }

        Push-Location (Join-Path $projectRoot "frontend")
        try {
            npm audit --omit=dev
            Assert-LastExitCode "Frontend production dependency audit"
            npm test
            Assert-LastExitCode "Frontend tests"
        }
        finally {
            Pop-Location
        }
    }

    Push-Location (Join-Path $projectRoot "frontend")
    try {
        npm run build
        Assert-LastExitCode "Frontend build"
    }
    finally {
        Pop-Location
    }

    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Push-Location $projectRoot
    try {
        tar -czf $archivePath `
            --exclude="backend/.venv" `
            --exclude="backend/.env" `
            --exclude="backend/instance" `
            --exclude="backend/uploads" `
            --exclude="backend/.pytest_cache" `
            --exclude="*/__pycache__" `
            --exclude="*.pyc" `
            backend deploy frontend/dist
        Assert-LastExitCode "Release packaging"
    }
    finally {
        Pop-Location
    }

    scp $archivePath "${SshUser}@${Server}:$remoteArchive"
    Assert-LastExitCode "Archive upload"
    scp (Join-Path $projectRoot "deploy\release-server.sh") "${SshUser}@${Server}:$remoteScript"
    Assert-LastExitCode "Release helper upload"

    $remoteDatabaseArgument = " ''"
    $remoteAssetsArgument = " ''"
    if ($SyncDemoDatabase) {
        $sourceDatabase = Join-Path $projectRoot "backend\instance\health_system.db"
        if (-not (Test-Path -LiteralPath $sourceDatabase -PathType Leaf)) {
            throw "Synthetic demo database not found: $sourceDatabase"
        }
        Remove-Item -LiteralPath $demoSnapshotPath -Force -ErrorAction SilentlyContinue
        $snapshotCode = @"
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("snapshot integrity check failed")
    if target.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("snapshot foreign-key check failed")
finally:
    target.close()
    source.close()
"@
        & (Join-Path $projectRoot "backend\.venv\Scripts\python.exe") -c $snapshotCode $sourceDatabase $demoSnapshotPath
        Assert-LastExitCode "Demo database snapshot"
        scp $demoSnapshotPath "${SshUser}@${Server}:$remoteDemoSnapshot"
        Assert-LastExitCode "Demo database upload"
        $remoteDatabaseArgument = " '$remoteDemoSnapshot'"

        $uploadsRoot = Join-Path $projectRoot "backend\uploads"
        $requiredDemoAssetDirectories = @(
            (Join-Path $uploadsRoot "institutions\demo-v7"),
            (Join-Path $uploadsRoot "health-assets\demo-v7")
        )
        foreach ($directory in $requiredDemoAssetDirectories) {
            if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
                throw "Synthetic demo asset directory not found: $directory"
            }
        }
        Remove-Item -LiteralPath $demoAssetsPath -Force -ErrorAction SilentlyContinue
        tar -czf $demoAssetsPath -C $uploadsRoot institutions/demo-v7 health-assets/demo-v7
        Assert-LastExitCode "Demo asset packaging"
        scp $demoAssetsPath "${SshUser}@${Server}:$remoteDemoAssets"
        Assert-LastExitCode "Demo asset upload"
        $remoteAssetsArgument = " '$remoteDemoAssets'"
    }

    $remoteMailArgument = " ''"
    if ($SyncMailSettings) {
        $localEnvPath = Join-Path $projectRoot "backend\.env"
        if (-not (Test-Path -LiteralPath $localEnvPath -PathType Leaf)) {
            throw "Local backend/.env is required for -SyncMailSettings."
        }
        $allowedMailKeys = @(
            "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
            "SMTP_FROM", "SMTP_USE_TLS", "NOTIFICATION_EMAIL_DRY_RUN",
            "NOTIFICATION_EMAIL_REDIRECT"
        )
        $mailValues = @{}
        foreach ($line in [System.IO.File]::ReadAllLines($localEnvPath)) {
            if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$' -and $allowedMailKeys -contains $Matches[1]) {
                $mailValues[$Matches[1]] = $Matches[2]
            }
        }
        foreach ($requiredKey in @("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM")) {
            if (-not $mailValues.ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace($mailValues[$requiredKey])) {
                throw "Local mail setting $requiredKey is missing; refusing server mail sync."
            }
        }
        if ($mailValues["NOTIFICATION_EMAIL_DRY_RUN"] -ne "0") {
            throw "NOTIFICATION_EMAIL_DRY_RUN must be 0 before server mail sync."
        }
        $mailLines = foreach ($key in $allowedMailKeys) {
            if ($mailValues.ContainsKey($key)) { "$key=$($mailValues[$key])" }
        }
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        # The remote parser is Bash. Always emit LF-only text even on Windows so
        # SMTP host names and credentials never retain a trailing carriage return.
        [System.IO.File]::WriteAllText(
            $mailSettingsPath,
            (($mailLines -join "`n") + "`n"),
            $utf8NoBom
        )
        scp $mailSettingsPath "${SshUser}@${Server}:$remoteMailSettings"
        Assert-LastExitCode "Mail settings upload"
        $remoteMailArgument = " '$remoteMailSettings'"
    }

    ssh -t "${SshUser}@${Server}" "bash -n '$remoteScript' && chmod 700 '$remoteScript' && sudo bash '$remoteScript' '$remoteArchive' '$releaseId'$remoteDatabaseArgument$remoteAssetsArgument$remoteMailArgument"
    Assert-LastExitCode "Remote release"

    Write-Host "Deployment completed: http://$Server"
}
finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $demoSnapshotPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $demoAssetsPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $mailSettingsPath -Force -ErrorAction SilentlyContinue
}
