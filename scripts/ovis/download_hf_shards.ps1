param(
    [Parameter(Mandatory = $true)] [string]$Repo,
    [Parameter(Mandatory = $true)] [string]$Destination,
    [string]$Revision = "main",
    [int64]$ChunkBytes = 67108864,
    [int]$Concurrency = 8
)

$ErrorActionPreference = "Stop"
$api = "https://huggingface.co/api/models/$Repo"
$files = (Invoke-RestMethod $api).siblings.rfilename | Where-Object { $_ -like "*.safetensors" }
if (-not $files) {
    $files = @(
        "model-00001-of-00003.safetensors",
        "model-00002-of-00003.safetensors",
        "model-00003-of-00003.safetensors"
    )
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

foreach ($name in $files) {
    $target = Join-Path $Destination $name
    $head = curl.exe -sIL "https://huggingface.co/$Repo/resolve/$Revision/$name"
    $sizeLine = $head | Select-String -Pattern "^X-Linked-Size:" | Select-Object -Last 1
    if (-not $sizeLine) { throw "Could not determine size for $name" }
    $size = [int64](($sizeLine.ToString() -replace "^X-Linked-Size:\s*", "").Trim())
    $partDir = Join-Path $Destination ("." + $name + ".parts")
    New-Item -ItemType Directory -Force -Path $partDir | Out-Null
    $jobs = @()
    for ($start = 0; $start -lt $size; $start += $ChunkBytes) {
        $end = [Math]::Min($size - 1, $start + $ChunkBytes - 1)
        $part = Join-Path $partDir ("{0:D8}.part" -f [int]($start / $ChunkBytes))
        if ((Test-Path $part) -and ((Get-Item $part).Length -eq ($end - $start + 1))) { continue }
        while ($jobs.Count -ge $Concurrency) {
            $jobs = @($jobs | Where-Object { -not $_.HasExited })
            if ($jobs.Count -ge $Concurrency) { Start-Sleep -Seconds 1 }
        }
        $url = "https://huggingface.co/$Repo/resolve/$Revision/$name"
        $args = @("-L", "--fail", "--retry", "10", "--retry-delay", "3", "--range", "$start-$end", "--output", $part, $url)
        $jobs += Start-Process -FilePath "curl.exe" -ArgumentList $args -WindowStyle Hidden -PassThru
    }
    while ($jobs.Count -gt 0) {
        $jobs = @($jobs | Where-Object { -not $_.HasExited })
        if ($jobs.Count -gt 0) { Start-Sleep -Seconds 1 }
    }
    for ($start = 0; $start -lt $size; $start += $ChunkBytes) {
        $end = [Math]::Min($size - 1, $start + $ChunkBytes - 1)
        $part = Join-Path $partDir ("{0:D8}.part" -f [int]($start / $ChunkBytes))
        $expected = $end - $start + 1
        if (-not (Test-Path $part) -or (Get-Item $part).Length -ne $expected) {
            throw "Incomplete range for $name at byte $start (expected $expected bytes)"
        }
    }
    $stream = [System.IO.File]::Open($target, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        for ($start = 0; $start -lt $size; $start += $ChunkBytes) {
            $part = Join-Path $partDir ("{0:D8}.part" -f [int]($start / $ChunkBytes))
            $input = [System.IO.File]::OpenRead($part)
            try { $input.CopyTo($stream) } finally { $input.Dispose() }
        }
    } finally { $stream.Dispose() }
    if ((Get-Item $target).Length -ne $size) { throw "Size check failed for $name" }
}
