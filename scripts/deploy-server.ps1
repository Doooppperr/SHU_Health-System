#requires -Version 7.0

param(
    [string]$Server = "111.229.87.94",
    [string]$SshUser = "ubuntu",
    [switch]$SkipTests,
    [switch]$SyncDemoDatabase,
    [switch]$SyncDemoMedia,
    [switch]$SyncMailSettings
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if ($Server -notmatch '^[A-Za-z0-9.-]+$' -or $SshUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "Server and SSH user contain unsupported characters."
}

function Assert-ReleaseSourceState([string]$ExpectedCommit = "") {
    & git -C $projectRoot fetch origin main
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to fetch origin/main before deployment."
    }
    $currentBranch = (& git -C $projectRoot branch --show-current).Trim()
    if ($currentBranch -ne "main") {
        throw "Production deployment is only allowed from main; current branch is $currentBranch."
    }
    $workingTreeChanges = @(& git -C $projectRoot status --porcelain --untracked-files=all)
    if ($workingTreeChanges.Count -gt 0) {
        throw "Production deployment requires a clean working tree."
    }
    $headCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
    $originMainCommit = (& git -C $projectRoot rev-parse origin/main).Trim()
    if ($headCommit -notmatch '^[0-9a-f]{40}$' -or $headCommit -ne $originMainCommit) {
        throw "Local main must exactly match origin/main before deployment."
    }
    if ($ExpectedCommit -and $headCommit -ne $ExpectedCommit) {
        throw "Release source changed while validation was running."
    }
    return $headCommit
}
$releaseCommit = Assert-ReleaseSourceState
$releaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$archiveName = "healthdoc-app-$releaseId.tar.gz"
$archivePath = Join-Path ([System.IO.Path]::GetTempPath()) $archiveName
$sourceArchivePath = Join-Path ([System.IO.Path]::GetTempPath()) "healthdoc-source-$releaseId.tar"
$remoteArchive = "/home/$SshUser/$archiveName"
$remoteScript = "/home/$SshUser/healthdoc-release-server.sh"
$demoSnapshotName = "healthdoc-demo-$releaseId.db"
$demoSnapshotPath = Join-Path ([System.IO.Path]::GetTempPath()) $demoSnapshotName
$remoteDemoSnapshot = "/home/$SshUser/$demoSnapshotName"
$demoAssetsName = "healthdoc-demo-assets-$releaseId.tar.gz"
$demoAssetsPath = Join-Path ([System.IO.Path]::GetTempPath()) $demoAssetsName
$demoAssetsStageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "healthdoc-demo-assets-stage-$releaseId"
$remoteDemoAssets = "/home/$SshUser/$demoAssetsName"
$mailSettingsName = "healthdoc-mail-$releaseId.env"
$mailSettingsPath = Join-Path ([System.IO.Path]::GetTempPath()) $mailSettingsName
$remoteMailSettings = "/home/$SshUser/$mailSettingsName"
$releaseStageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "healthdoc-release-stage-$releaseId"
$frontendBuildPath = Join-Path $releaseStageRoot "frontend\dist"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Invoke-ReleaseJson([string]$Uri, [string]$Step) {
    $lastFailure = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            return Invoke-RestMethod `
                -Uri $Uri `
                -Method Get `
                -Headers @{ "Cache-Control" = "no-cache" } `
                -TimeoutSec 20
        }
        catch {
            $lastFailure = $_
            if ($attempt -lt 3) {
                Start-Sleep -Seconds 2
            }
        }
    }
    throw "$Step failed after three attempts: $($lastFailure.Exception.Message)"
}

function Test-ExactSchemaVersion13($Value) {
    return (
        (($Value -is [int]) -or ($Value -is [long])) -and
        ([long]$Value -eq 13)
    )
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
            npm test -- --configLoader runner
            Assert-LastExitCode "Frontend tests"
            npm run test:e2e
            Assert-LastExitCode "Frontend Playwright critical paths"
        }
        finally {
            Pop-Location
        }
    }

    [void](Assert-ReleaseSourceState $releaseCommit)

    Push-Location (Join-Path $projectRoot "frontend")
    try {
        $env:VITE_RELEASE_COMMIT = $releaseCommit
        npm run build -- --configLoader runner --outDir $frontendBuildPath --emptyOutDir
        Assert-LastExitCode "Frontend build"
    }
    finally {
        Remove-Item Env:VITE_RELEASE_COMMIT -ErrorAction SilentlyContinue
        Pop-Location
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $frontendBuildPath "release.json"),
        "{`"release_commit`":`"$releaseCommit`",`"schema_version`":13}`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $releaseStageRoot "RELEASE_COMMIT"),
        "$releaseCommit`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
    $embeddedCommitMatches = @(
        Get-ChildItem -LiteralPath (Join-Path $frontendBuildPath "assets") -Filter "*.js" -File |
            Select-String -SimpleMatch $releaseCommit -List
    )
    if ($embeddedCommitMatches.Count -eq 0) {
        throw "Frontend bundles do not contain the expected release commit."
    }

    [void](Assert-ReleaseSourceState $releaseCommit)

    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $sourceArchivePath -Force -ErrorAction SilentlyContinue
    $releaseSourcePaths = @(
        "backend/app",
        "backend/migrations",
        "backend/rag_sources",
        "backend/scripts",
        "backend/report_media_manifest.json",
        "backend/.env.example",
        "backend/README.md",
        "backend/requirements.txt",
        "backend/mcp_server.py",
        "backend/run.py",
        "backend/wsgi.py",
        "deploy"
    )
    Push-Location $projectRoot
    try {
        # Package the exact committed bytes instead of the Windows working tree.
        # This preserves canonical LF content, which is required by the RAG
        # approved_sha256 integrity check on Linux.
        git archive --format=tar --output=$sourceArchivePath $releaseCommit -- $releaseSourcePaths
        Assert-LastExitCode "Committed source archive"
        tar -xf $sourceArchivePath -C $releaseStageRoot
        Assert-LastExitCode "Committed source extraction"
        tar -czf $archivePath -C $releaseStageRoot backend deploy RELEASE_COMMIT frontend/dist
        Assert-LastExitCode "Release packaging"
    }
    finally {
        Pop-Location
    }

    [void](Assert-ReleaseSourceState $releaseCommit)

    scp $archivePath "${SshUser}@${Server}:$remoteArchive"
    Assert-LastExitCode "Archive upload"
    ssh -o BatchMode=yes "${SshUser}@${Server}" "chmod 600 '$remoteArchive'"
    Assert-LastExitCode "Archive permission hardening"
    scp (Join-Path $releaseStageRoot "deploy\release-server.sh") "${SshUser}@${Server}:$remoteScript"
    Assert-LastExitCode "Release helper upload"
    ssh -o BatchMode=yes "${SshUser}@${Server}" "chmod 700 '$remoteScript'"
    Assert-LastExitCode "Release helper permission hardening"

    $remoteDatabaseArgument = " ''"
    $remoteAssetsArgument = " ''"
    if ($SyncDemoDatabase) {
        $sourceDatabase = Join-Path $projectRoot "backend\instance\health_system.db"
        if (-not (Test-Path -LiteralPath $sourceDatabase -PathType Leaf)) {
            throw "Demo database not found: $sourceDatabase"
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
        & (Join-Path $projectRoot "backend\.venv\Scripts\python.exe") `
            (Join-Path $projectRoot "backend\scripts\validate_v13_demo.py") `
            --database $demoSnapshotPath `
            --upload-dir (Join-Path $projectRoot "backend\uploads")
        Assert-LastExitCode "Snapshotted business dataset alignment validation"
        scp $demoSnapshotPath "${SshUser}@${Server}:$remoteDemoSnapshot"
        Assert-LastExitCode "Demo database upload"
        ssh -o BatchMode=yes "${SshUser}@${Server}" "chmod 600 '$remoteDemoSnapshot'"
        Assert-LastExitCode "Demo database permission hardening"
        $remoteDatabaseArgument = " '$remoteDemoSnapshot'"

    }

    if ($SyncDemoDatabase -or $SyncDemoMedia) {
        $uploadsRoot = Join-Path $projectRoot "backend\uploads"
        $requiredDemoAssetDirectories = @(
            (Join-Path $uploadsRoot "institutions\demo-v8"),
            (Join-Path $uploadsRoot "health-assets\demo-v8"),
            (Join-Path $uploadsRoot "health-assets\demo-v10")
        )
        foreach ($directory in $requiredDemoAssetDirectories) {
            if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
                throw "Report media directory not found: $directory"
            }
        }
        & (Join-Path $projectRoot "backend\.venv\Scripts\python.exe") `
            (Join-Path $projectRoot "backend\scripts\refresh_demo_media.py") --check-only
        Assert-LastExitCode "Demo media validation"
        Remove-Item -LiteralPath $demoAssetsPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $demoAssetsStageRoot -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $demoAssetsStageRoot -Force | Out-Null
        $mediaManifestPath = Join-Path $projectRoot "backend\report_media_manifest.json"
        $mediaManifest = Get-Content -LiteralPath $mediaManifestPath -Raw | ConvertFrom-Json
        if ($mediaManifest.version -ne 13 -or $null -eq $mediaManifest.items -or $mediaManifest.items.Count -eq 0) {
            throw "Schema-v13 report media manifest is missing or empty."
        }
        $uploadsCanonical = [System.IO.Path]::GetFullPath($uploadsRoot)
        $approvedStorageKeys = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::Ordinal
        )
        foreach ($item in $mediaManifest.items) {
            $storageKey = [string]$item.storage_key
            $pathParts = $storageKey -split "/"
            $hasTraversal = $pathParts -contains ".."
            $hasApprovedPrefix = (
                $storageKey.StartsWith("institutions/demo-v8/", [System.StringComparison]::Ordinal) -or
                $storageKey.StartsWith("health-assets/demo-v8/", [System.StringComparison]::Ordinal) -or
                $storageKey.StartsWith("health-assets/demo-v10/", [System.StringComparison]::Ordinal)
            )
            if (
                [string]::IsNullOrWhiteSpace($storageKey) -or
                [System.IO.Path]::IsPathRooted($storageKey) -or
                $hasTraversal -or
                -not $hasApprovedPrefix -or
                -not $approvedStorageKeys.Add($storageKey)
            ) {
                throw "Unsafe or duplicate report media manifest path: $storageKey"
            }
            $relativePath = $storageKey.Replace("/", [System.IO.Path]::DirectorySeparatorChar)
            $sourcePath = [System.IO.Path]::GetFullPath((Join-Path $uploadsRoot $relativePath))
            if (-not $sourcePath.StartsWith(
                "$uploadsCanonical$([System.IO.Path]::DirectorySeparatorChar)",
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Report media path leaves the upload root: $storageKey"
            }
            if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
                throw "Manifest-approved report media is missing: $storageKey"
            }
            $destinationPath = Join-Path $demoAssetsStageRoot $relativePath
            New-Item -ItemType Directory -Path (Split-Path -Parent $destinationPath) -Force | Out-Null
            Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
        }
        tar -czf $demoAssetsPath -C $demoAssetsStageRoot institutions/demo-v8 health-assets/demo-v8 health-assets/demo-v10
        Assert-LastExitCode "Demo asset packaging"
        scp $demoAssetsPath "${SshUser}@${Server}:$remoteDemoAssets"
        Assert-LastExitCode "Demo asset upload"
        ssh -o BatchMode=yes "${SshUser}@${Server}" "chmod 600 '$remoteDemoAssets'"
        Assert-LastExitCode "Demo asset permission hardening"
        $remoteAssetsArgument = " '$remoteDemoAssets'"
    }

    $remoteMailArgument = " ''"
    if ($SyncMailSettings) {
        $localEnvPath = Join-Path $projectRoot "backend\.env"
        if (-not (Test-Path -LiteralPath $localEnvPath -PathType Leaf)) {
            throw "Local backend/.env is required for -SyncMailSettings."
        }
        $mailExtractionCode = @'
import json
import sys

from dotenv import dotenv_values


allowed = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
    "SMTP_FROM", "SMTP_USE_TLS", "NOTIFICATION_EMAIL_DRY_RUN",
    "NOTIFICATION_EMAIL_REDIRECT",
)
values = dotenv_values(sys.argv[1])
payload = {
    key: str(values[key])
    for key in allowed
    if values.get(key) is not None
}
for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"):
    if not payload.get(key, "").strip():
        raise RuntimeError(f"Local mail setting {key} is missing")
if payload.get("NOTIFICATION_EMAIL_DRY_RUN") != "0":
    raise RuntimeError("NOTIFICATION_EMAIL_DRY_RUN must be 0")
if payload.get("NOTIFICATION_EMAIL_REDIRECT", "").strip():
    raise RuntimeError("NOTIFICATION_EMAIL_REDIRECT must be empty")
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as output:
    json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
    output.write("\n")
'@
        & (Join-Path $projectRoot "backend\.venv\Scripts\python.exe") `
            -c $mailExtractionCode $localEnvPath $mailSettingsPath
        Assert-LastExitCode "Mail settings extraction"
        scp $mailSettingsPath "${SshUser}@${Server}:$remoteMailSettings"
        Assert-LastExitCode "Mail settings upload"
        ssh -o BatchMode=yes "${SshUser}@${Server}" "chmod 600 '$remoteMailSettings'"
        Assert-LastExitCode "Mail settings permission hardening"
        $remoteMailArgument = " '$remoteMailSettings'"
    }

    # The release helper is fully non-interactive. Avoid forcing a TTY here:
    # Windows OpenSSH can otherwise keep the client session open after the
    # remote release has already completed. BatchMode also prevents an
    # accidental password prompt, while sudo -n fails fast when the host is
    # not configured for unattended releases.
    [void](Assert-ReleaseSourceState $releaseCommit)
    $publicAppUrl = "http://$Server"
    ssh -o BatchMode=yes "${SshUser}@${Server}" "bash -n '$remoteScript' && chmod 700 '$remoteScript' && sudo -n bash '$remoteScript' '$remoteArchive' '$releaseId'$remoteDatabaseArgument$remoteAssetsArgument$remoteMailArgument '$releaseCommit' '$publicAppUrl'"
    Assert-LastExitCode "Remote release"

    # The public release is already committed. These checks are deliberately
    # read-only: drift requires a new release and must never trigger a cold
    # rollback that could discard acknowledged writes.
    [void](Assert-ReleaseSourceState $releaseCommit)
    $healthPayload = Invoke-ReleaseJson `
        "$publicAppUrl/api/health?release_check=$releaseId" `
        "Published API version check"
    if (
        [string]$healthPayload.status -ne "ok" -or
        -not (Test-ExactSchemaVersion13 $healthPayload.schema_version) -or
        [string]$healthPayload.release_commit -ne $releaseCommit
    ) {
        throw "Published API does not match release $releaseCommit and schema v13."
    }
    $frontendPayload = Invoke-ReleaseJson `
        "$publicAppUrl/release.json?release_check=$releaseId" `
        "Published frontend version check"
    if (
        -not (Test-ExactSchemaVersion13 $frontendPayload.schema_version) -or
        [string]$frontendPayload.release_commit -ne $releaseCommit
    ) {
        throw "Published frontend does not match release $releaseCommit and schema v13."
    }
    [void](Assert-ReleaseSourceState $releaseCommit)

    Write-Host "Deployment completed: http://$Server"
}
finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $sourceArchivePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $demoSnapshotPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $demoAssetsPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $demoAssetsStageRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $mailSettingsPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $releaseStageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
