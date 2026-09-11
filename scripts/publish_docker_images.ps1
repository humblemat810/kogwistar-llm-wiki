[CmdletBinding()]
param(
    [string]$DockerHubUser = $env:DOCKERHUB_USERNAME,
    [string]$Tag = "latest",
    [ValidateSet("cu128", "cpu")]
    [string]$RepresentationBackend = "cu128",
    [switch]$Login,
    [switch]$SkipRepresentation
)

$ErrorActionPreference = "Stop"

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Invoke-Docker @("info")

if ([string]::IsNullOrWhiteSpace($DockerHubUser)) {
    # Docker Desktop commonly reports the authenticated Docker Hub account in
    # `docker info`; the credential helper itself intentionally does not expose
    # credentials to this script.
    $dockerInfo = & docker info 2>$null
    $usernameLine = $dockerInfo | Select-String -Pattern '^\s*Username:\s*(\S+)\s*$' | Select-Object -First 1
    if ($usernameLine) {
        $DockerHubUser = $usernameLine.Matches[0].Groups[1].Value
    }
}

if ([string]::IsNullOrWhiteSpace($DockerHubUser) -and [Environment]::UserInteractive) {
    $DockerHubUser = Read-Host "Docker Hub username for the image namespace"
}

if ([string]::IsNullOrWhiteSpace($DockerHubUser)) {
    throw "Docker is authenticated, but the Docker Hub namespace is unknown. Pass -DockerHubUser or set DOCKERHUB_USERNAME."
}

if ($Login) {
    # Docker Desktop normally opens or guides an OAuth/device-code login.
    # Credentials remain in Docker's configured credential store.
    Invoke-Docker @("login")
}

$appImage = "$DockerHubUser/kogwistar-llm-wiki:$Tag"
Write-Host "Building $appImage"
Invoke-Docker @("build", "--tag", $appImage, "--file", "Dockerfile", ".")
Write-Host "Pushing $appImage"
Invoke-Docker @("push", $appImage)

if (-not $SkipRepresentation) {
    $representationTag = if ($RepresentationBackend -eq "cpu" -and $Tag -eq "latest") {
        "latest-cpu"
    } elseif ($RepresentationBackend -eq "cpu") {
        "$Tag-cpu"
    } else {
        $Tag
    }
    $representationImage = "$DockerHubUser/kogwistar-llm-wiki-embedding:$representationTag"
    Write-Host "Building $representationImage ($RepresentationBackend)"
    Invoke-Docker @(
        "build",
        "--build-arg", "LLM_WIKI_REPRESENTATION_TORCH_BACKEND=$RepresentationBackend",
        "--tag", $representationImage,
        "--file", "Dockerfile.representation-service",
        "."
    )
    Write-Host "Pushing $representationImage"
    Invoke-Docker @("push", $representationImage)
}

Write-Host "Images published under Docker Hub namespace '$DockerHubUser'."
