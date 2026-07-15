param(
    [string]$Server = "111.229.87.94",
    [string]$SshUser = "ubuntu",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$releaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$archiveName = "healthdoc-app-$releaseId.tar.gz"
$archivePath = Join-Path ([System.IO.Path]::GetTempPath()) $archiveName
$remoteArchive = "/home/$SshUser/$archiveName"
$remoteScript = "/home/$SshUser/healthdoc-release-server.sh"

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

    ssh -t "${SshUser}@${Server}" "chmod 700 '$remoteScript' && sudo bash '$remoteScript' '$remoteArchive' '$releaseId'"
    Assert-LastExitCode "Remote release"

    Write-Host "Deployment completed: http://$Server"
}
finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
}
