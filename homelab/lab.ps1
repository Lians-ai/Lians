[CmdletBinding()]
param(
    [ValidateSet("up", "up-real", "verify", "verify-real", "status", "logs", "logs-real", "proof", "down", "reset")]
    [string]$Command = "up",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$LabRoot = $PSScriptRoot
$ComposeFile = Join-Path $LabRoot "compose.yaml"
$RealModelFile = Join-Path $LabRoot "compose.real-model.yaml"
$EnvFile = Join-Path $LabRoot ".env"
$ExampleEnv = Join-Path $LabRoot ".env.example"
$Artifacts = Join-Path $LabRoot "artifacts"

function Get-LabRevision {
    $repoRoot = Split-Path -Parent $LabRoot
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return "unrecorded" }
    $commit = (& git -C $repoRoot rev-parse --verify HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $commit) { return "unrecorded" }
    $dirty = (& git -C $repoRoot status --porcelain --untracked-files=normal 2>$null)
    if ($dirty) { return "$($commit.Trim())-dirty" }
    return $commit.Trim()
}

function New-LabEnvironment {
    if (Test-Path -LiteralPath $EnvFile) { return }

    $adminBytes = New-Object byte[] 32
    $masterBytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($adminBytes)
        $rng.GetBytes($masterBytes)
    }
    finally {
        $rng.Dispose()
    }
    $adminSecret = ([BitConverter]::ToString($adminBytes)).Replace("-", "").ToLowerInvariant()
    $masterKey = [Convert]::ToBase64String($masterBytes)

    $content = Get-Content -Raw -LiteralPath $ExampleEnv
    $content = $content -replace '(?m)^LIANS_ADMIN_SECRET=.*$', "LIANS_ADMIN_SECRET=$adminSecret"
    $content = $content -replace '(?m)^LIANS_MASTER_ENCRYPTION_KEY=.*$', "LIANS_MASTER_ENCRYPTION_KEY=$masterKey"
    [IO.File]::WriteAllText($EnvFile, $content, [Text.UTF8Encoding]::new($false))
    Write-Host "Created homelab/.env with random local secrets."
}

function Assert-DockerEngine {
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Desktop is installed but its Linux engine is not running. Start Docker Desktop, then retry."
    }
}

function Invoke-Compose {
    param(
        [string[]]$Arguments,
        [switch]$RealModel
    )
    $base = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
    if ($RealModel) {
        $base += @("--project-name", "lians-homelab-real", "-f", $RealModelFile)
    }
    & docker @base @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Show-Endpoints {
    Write-Host ""
    Write-Host "Lians API   http://localhost:8001/docs"
    Write-Host "Grafana     http://localhost:3000/d/lians-homelab-proof"
    Write-Host "Prometheus  http://localhost:9090"
    Write-Host "Alloy       http://localhost:12345"
    Write-Host ""
    Write-Host "Grafana credentials are in homelab/.env."
}

if ($Command -eq "proof") {
    $latestProof = Join-Path $Artifacts "latest-receipt.json"
    if (-not (Test-Path -LiteralPath $latestProof)) {
        throw "No exported proof exists yet. Run '.\lab.ps1 up' first."
    }
    Get-Content -Raw -LiteralPath $latestProof
    return
}

New-LabEnvironment
New-Item -ItemType Directory -Force -Path $Artifacts | Out-Null
$env:LAB_GIT_COMMIT = Get-LabRevision
Assert-DockerEngine

switch ($Command) {
    "up" {
        Invoke-Compose -RealModel -Arguments @("down", "--remove-orphans")
        Invoke-Compose -Arguments @("up", "--build", "-d")
        Invoke-Compose -Arguments @("--profile", "tools", "run", "--rm", "verify")
        Show-Endpoints
    }
    "up-real" {
        Invoke-Compose -Arguments @("down", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("up", "--build", "-d")
        Invoke-Compose -RealModel -Arguments @("--profile", "tools", "run", "--rm", "verify")
        Show-Endpoints
    }
    "verify" {
        Invoke-Compose -Arguments @("--profile", "tools", "run", "--rm", "verify")
    }
    "verify-real" {
        Invoke-Compose -RealModel -Arguments @("--profile", "tools", "run", "--rm", "verify")
    }
    "status" {
        Write-Host "Lightweight project"
        Invoke-Compose -Arguments @("ps")
        Write-Host "Real-model project"
        Invoke-Compose -RealModel -Arguments @("ps")
        Show-Endpoints
    }
    "logs" {
        Invoke-Compose -Arguments @("logs", "--follow", "--tail", "200")
    }
    "logs-real" {
        Invoke-Compose -RealModel -Arguments @("logs", "--follow", "--tail", "200")
    }
    "down" {
        Invoke-Compose -Arguments @("down", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("down", "--remove-orphans")
    }
    "reset" {
        if (-not $Force) {
            $answer = Read-Host "Reset deletes all homelab databases, telemetry, and proof state. Type RESET to continue"
            if ($answer -ne "RESET") { throw "Reset cancelled." }
        }
        Invoke-Compose -Arguments @("down", "--volumes", "--remove-orphans")
        Invoke-Compose -RealModel -Arguments @("down", "--volumes", "--remove-orphans")
        Write-Host "Removed the homelab containers and named volumes. homelab/.env and exported artifacts were preserved."
    }
}
