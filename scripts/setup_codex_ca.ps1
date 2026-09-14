[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $env:TEMP "llm-wiki-codex-ca\ca-bundle.pem"),
    [string]$ComposeProject = "llm-wiki-memory",
    [switch]$StartCompose
)

$ErrorActionPreference = "Stop"

function Add-StoreCertificates([string]$StorePath, [string]$Destination) {
    $count = 0
    foreach ($certificate in Get-ChildItem -Path $StorePath) {
        if ($certificate.PSIsContainer -or $certificate.HasPrivateKey) { continue }
        try {
            Export-Certificate -Cert $certificate -FilePath $Destination -Type CERT -Force | Out-Null
            $der = [IO.File]::ReadAllBytes($Destination)
            $base64 = [Convert]::ToBase64String($der)
            Add-Content -LiteralPath $script:Bundle -Value "-----BEGIN CERTIFICATE-----"
            for ($offset = 0; $offset -lt $base64.Length; $offset += 64) {
                Add-Content -LiteralPath $script:Bundle -Value $base64.Substring($offset, [Math]::Min(64, $base64.Length - $offset))
            }
            Add-Content -LiteralPath $script:Bundle -Value "-----END CERTIFICATE-----"
            $count++
        } catch {
            Write-Verbose "Skipping $($certificate.Subject): $($_.Exception.Message)"
        }
    }
    return $count
}

$directory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$script:Bundle = [IO.Path]::GetFullPath($OutputPath)
$temporaryCertificate = Join-Path $directory "certificate.der"
Remove-Item -LiteralPath $script:Bundle -Force -ErrorAction SilentlyContinue
$machineCount = Add-StoreCertificates "Cert:\LocalMachine\Root" $temporaryCertificate
$userCount = Add-StoreCertificates "Cert:\CurrentUser\Root" $temporaryCertificate
Remove-Item -LiteralPath $temporaryCertificate -Force -ErrorAction SilentlyContinue

if ($machineCount + $userCount -eq 0) {
    throw "No trusted public root certificates could be exported from Windows certificate stores."
}

Write-Output "Created $script:Bundle from $($machineCount + $userCount) Windows trusted roots."
Write-Output "This bundle contains public certificates only; no private keys or credentials."
$env:LLM_WIKI_CODEX_CA_BUNDLE_FILE = $script:Bundle

if ($StartCompose) {
    & docker compose -p $ComposeProject `
        -f compose.yml -f compose.memory-agent.yml -f compose.codex.yml `
        -f compose.codex-memory.yml -f compose.codex-ca.yml up -d
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed with exit code $LASTEXITCODE" }
} else {
    Write-Output "For this PowerShell session: `$env:LLM_WIKI_CODEX_CA_BUNDLE_FILE = '$script:Bundle'"
    Write-Output "Add compose.codex-ca.yml to the Compose files when starting the stack."
}
