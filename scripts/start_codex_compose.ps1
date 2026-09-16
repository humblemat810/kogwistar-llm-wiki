[CmdletBinding()]
param(
    [ValidateSet("standalone", "memory")]
    [string]$Mode = "standalone",
    [string]$Project = "llm-wiki-memory",
    [switch]$Build,
    [switch]$Login,
    [ValidateSet("split", "combined")]
    [string]$Stack = "split",
    [ValidateSet("none", "vllm", "transformers")]
    [string]$Embedding = "none",
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$bundle = Join-Path $env:TEMP "llm-wiki-codex-ca\ca-bundle.pem"
& (Join-Path $PSScriptRoot "setup_codex_ca.ps1") -OutputPath $bundle
if (-not $?) { throw "Codex CA setup failed" }
$env:LLM_WIKI_CODEX_CA_BUNDLE_FILE = [IO.Path]::GetFullPath($bundle)

$files = @("-f", "compose.codex.yml", "-f", "compose.codex-ca.yml")
if ($Mode -eq "memory") {
    $files = @("-f", "compose.yml", "-f", "compose.memory-agent.yml") + $files + @("-f", "compose.codex-memory.yml")
    if ($Stack -eq "combined") { $files += @("-f", "compose.combined.yml") }
    if ($Embedding -eq "vllm") { $files += @("-f", "compose.embedding-vllm.yml") }
    if ($Embedding -eq "transformers") { $files += @("-f", "compose.multimodal.yml") }
}
$composeOptions = @()
if (Test-Path -LiteralPath $EnvFile) { $composeOptions += @("--env-file", $EnvFile) }
if ($Build) {
    & docker compose @composeOptions -p $Project @files build codex
    if ($LASTEXITCODE -ne 0) { throw "Codex image build failed with exit code $LASTEXITCODE" }
}
if ($Login) {
    & docker compose @composeOptions -p $Project @files run --rm -it --entrypoint codex codex login --device-auth
    if ($LASTEXITCODE -ne 0) { throw "Codex device login failed with exit code $LASTEXITCODE" }
}
& docker compose @composeOptions -p $Project @files up -d
if ($LASTEXITCODE -ne 0) { throw "Codex Compose startup failed with exit code $LASTEXITCODE" }
