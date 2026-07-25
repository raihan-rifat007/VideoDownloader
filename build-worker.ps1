[CmdletBinding()]
param(
    [string]$DockerDataPath = (Join-Path $env:LOCALAPPDATA "Docker\wsl\data\docker_data.vhdx"),
    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$mutex = $null
$ownsMutex = $false
$minimumFreeGB = 80

try {
    $createdNew = $false
    $mutex = [System.Threading.Mutex]::new(
        $false,
        "Global\ReClipAsrWorkerBuild",
        [ref]$createdNew
    )

    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
        throw "The previous worker build abandoned its lock. Check 'docker buildx history ls' and retry only after no build is Running."
    }

    if (-not $ownsMutex) {
        throw "Another ReClip worker build is already running. Wait for it to finish before retrying."
    }

    if (-not (Test-Path -LiteralPath $DockerDataPath -PathType Leaf)) {
        throw "Docker data VHDX not found at '$DockerDataPath'. Pass -DockerDataPath after relocating Docker Desktop storage."
    }

    $resolvedDockerDataPath = (Resolve-Path -LiteralPath $DockerDataPath).Path
    $dockerDataDrive = [System.IO.Path]::GetPathRoot($resolvedDockerDataPath)
    if ([string]::IsNullOrWhiteSpace($dockerDataDrive)) {
        throw "Could not determine the host drive for '$resolvedDockerDataPath'."
    }

    $driveInfo = [System.IO.DriveInfo]::new($dockerDataDrive)
    $freeGB = [math]::Floor($driveInfo.AvailableFreeSpace / 1GB)
    if ($freeGB -lt $minimumFreeGB) {
        throw "Worker build blocked: Docker data is on '$dockerDataDrive' with $freeGB GB free; at least $minimumFreeGB GB is required. Free space or relocate Docker Desktop storage, then retry."
    }

    Write-Host "Docker data drive $dockerDataDrive has $freeGB GB free."
    if ($PreflightOnly) {
        Write-Host "Worker build preflight passed; no build was started."
        return
    }

    Write-Host "Starting one serialized worker build."
    $workerBuildCompose = Join-Path $PSScriptRoot "docker-compose.worker-build.yml"
    & docker compose --project-directory $PSScriptRoot --parallel 1 -f $workerBuildCompose build asr-worker
    if ($LASTEXITCODE -ne 0) {
        throw "Worker image build failed with exit code $LASTEXITCODE."
    }
}
finally {
    if ($ownsMutex -and $null -ne $mutex) {
        $mutex.ReleaseMutex()
    }
    if ($null -ne $mutex) {
        $mutex.Dispose()
    }
}
