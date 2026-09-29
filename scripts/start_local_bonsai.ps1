[CmdletBinding()]
param(
    [string]$LlamaRoot = 'D:\prism-llama.cpp',
    [string]$ModelRoot = 'D:\models\bonsai2',
    [int]$Context = 8192,
    [int]$Port = 8181,
    [string]$HostAddress = '0.0.0.0'
)

$ErrorActionPreference = 'Stop'
$server = Join-Path $LlamaRoot 'build\bin\Release\llama-server.exe'
$model = Join-Path $ModelRoot 'Ternary-Bonsai-2-27B-PTQ1_0.gguf'
$mmproj = Join-Path $ModelRoot 'Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf'

foreach ($path in @($server, $model, $mmproj)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required Bonsai file was not found: $path"
    }
}

if ($Context -lt 1024) {
    throw 'Context must be at least 1024 tokens.'
}

Write-Host "Starting Bonsai llama-server on $HostAddress`:$Port with context $Context"
Write-Host "Model: $model"
Write-Host "Vision projector: $mmproj"

& $server `
    -m $model `
    --mmproj $mmproj `
    -c $Context `
    -ngl 999 `
    --parallel 1 `
    --cache-type-k q4_0 `
    --cache-type-v q4_0 `
    -fa on `
    --fit-target 800 `
    --host $HostAddress `
    --port $Port `
    -lv 4

exit $LASTEXITCODE
