[CmdletBinding()]
param(
    [string]$DockerHubUser = $env:DOCKERHUB_USERNAME,
    [string]$Tag = "latest",
    [ValidateSet("app", "embedding-cpu", "embedding-cuda12.8", "all")]
    [string]$Target = "app",
    [switch]$Login,
    [switch]$SkipEmbedding
)

$ErrorActionPreference = "Stop"

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-EmbeddingTag {
    param([Parameter(Mandatory = $true)][string]$Backend)
    if ($Tag -eq "latest") {
        return "latest-$Backend"
    }
    return "$Tag-$Backend"
}

function Publish-Embedding {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("cpu", "cuda12.8")][string]$Backend,
        [Parameter(Mandatory = $true)][string]$TorchBackend
    )

    $embeddingImage = "$DockerHubUser/kogwistar-llm-wiki-embedding:$(Get-EmbeddingTag $Backend)"
    Write-Host "Building $embeddingImage ($TorchBackend)"
    Invoke-Docker @(
        "buildx", "build", "--load",
        "--build-arg", "LLM_WIKI_EMBEDDING_TORCH_BACKEND=$TorchBackend",
        "--tag", $embeddingImage,
        "--file", "Dockerfile.embedding-service",
        "."
    )
    Write-Host "Pushing $embeddingImage"
    Invoke-Docker @("push", $embeddingImage)
}

if ($SkipEmbedding) {
    if ($Target -ne "app") {
        throw "-SkipEmbedding is only compatible with the default -Target app"
    }
    # Keep the old flag working while making the explicit target model primary.
    $Target = "app"
}

Invoke-Docker @("info")

if ([string]::IsNullOrWhiteSpace($DockerHubUser)) {
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
    Invoke-Docker @("login")
}

if ($Target -eq "app" -or $Target -eq "all") {
    $appImage = "$DockerHubUser/kogwistar-llm-wiki:$Tag"
    Write-Host "Building $appImage"
    Invoke-Docker @("buildx", "build", "--load", "--tag", $appImage, "--file", "Dockerfile", ".")
    Write-Host "Pushing $appImage"
    Invoke-Docker @("push", $appImage)
}

if ($Target -eq "embedding-cpu" -or $Target -eq "all") {
    Publish-Embedding -Backend "cpu" -TorchBackend "cpu"
}

if ($Target -eq "embedding-cuda12.8" -or $Target -eq "all") {
    Publish-Embedding -Backend "cuda12.8" -TorchBackend "cu128"
}

Write-Host "Images published under Docker Hub namespace '$DockerHubUser' for target '$Target'."
