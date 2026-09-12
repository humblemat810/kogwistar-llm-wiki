[CmdletBinding()]
param(
    [string]$DockerHubUser = $env:DOCKERHUB_USERNAME,
    [string]$Tag = "latest",
    [ValidateSet("app", "embedding-cpu", "embedding-cuda12.8", "all")]
    [string]$Target = "app",
    [switch]$Login,
    [switch]$SkipEmbedding,
    [switch]$AllowExistingTag
)

$ErrorActionPreference = "Stop"

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    # Docker Desktop can write harmless daemon warnings to stderr. Temporarily
    # allow those records through, then enforce the native exit code ourselves.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($exitCode -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

function Get-EmbeddingTag {
    param([Parameter(Mandatory = $true)][string]$Backend)
    if ($Tag -eq "latest") {
        return "latest-$Backend"
    }
    return "$Tag-$Backend"
}

function Test-RemoteImageTag {
    param([Parameter(Mandatory = $true)][string]$Image)
    $output = & docker buildx imagetools inspect $Image 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        return $true
    }
    $details = $output -join "`n"
    if ($details -match '(?i)no such manifest|manifest unknown|not found') {
        return $false
    }
    throw "Could not verify whether Docker tag exists: $Image`n$details"
}

function Get-PublishImages {
    $images = @()
    if ($Target -eq "app" -or $Target -eq "all") {
        $images += "$DockerHubUser/kogwistar-llm-wiki:$Tag"
    }
    if ($Target -eq "embedding-cpu" -or $Target -eq "all") {
        $images += "$DockerHubUser/kogwistar-llm-wiki-embedding:$(Get-EmbeddingTag 'cpu')"
    }
    if ($Target -eq "embedding-cuda12.8" -or $Target -eq "all") {
        $images += "$DockerHubUser/kogwistar-llm-wiki-embedding:$(Get-EmbeddingTag 'cuda12.8')"
    }
    return $images
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

& python (Join-Path $PSScriptRoot "verify_release_version.py") --tag $Tag
if ($LASTEXITCODE -ne 0) {
    throw "release version verification failed"
}

if (-not $AllowExistingTag) {
    foreach ($image in (Get-PublishImages)) {
        if (Test-RemoteImageTag $image) {
            throw "Docker tag already exists: $image; choose a new package release or pass -AllowExistingTag explicitly"
        }
    }
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
