[CmdletBinding()]
param(
    [string]$Image = "llm-wiki-pypy311-ci:local",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\")).Path

if (-not $SkipBuild) {
    docker build --file (Join-Path $root "Dockerfile.pypy311-ci") `
        --tag $Image $root
    if ($LASTEXITCODE -ne 0) { throw "PyPy test image build failed with exit code $LASTEXITCODE" }
}

docker run --rm -i `
    --env KOGWISTAR_IMPL_MODE=python `
    --env LLM_WIKI_AUTH_MODE=disabled `
    --env LLM_WIKI_OTEL_ENABLED=false `
    --env KOGWISTAR_LOG_LEVEL=WARNING `
    --env LOG_LEVEL=WARNING `
    --volume "${root}:/workspace" `
    --workdir /workspace `
    --entrypoint pypy3 $Image `
    scripts/run_pypy311_ci.py --venv /tmp/pypy311-venv
if ($LASTEXITCODE -ne 0) { throw "PyPy 3.11 Linux CI profile failed with exit code $LASTEXITCODE" }
