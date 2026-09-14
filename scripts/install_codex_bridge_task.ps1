param(
    [string]$Python = "python",
    [int]$Port = 8791,
    [string]$TokenEnv = "LLM_WIKI_CODEX_BRIDGE_TOKEN"
)

$ErrorActionPreference = "Stop"
$taskName = "LLM-Wiki Codex Bridge"
$arguments = "-m kogwistar_llm_wiki codex-bridge --port $Port --token-env $TokenEnv"
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType InteractiveToken -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Output "Installed '$taskName' for the current user. Set $TokenEnv in the user's environment before starting it."
