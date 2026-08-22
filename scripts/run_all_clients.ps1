# Runs the triage bot for every client under clients/ that has a .env file.
# One client failing does not stop the others — triage.py has its own
# crash handling and ops alerting per client.
#
# Windows Task Scheduler example: trigger every 10 minutes, action:
#   powershell.exe -File "C:\path\to\support-triage-bot\scripts\run_all_clients.ps1"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

Get-ChildItem -Path "clients" -Directory | ForEach-Object {
    $client = $_.Name
    $envFile = Join-Path $_.FullName ".env"
    if (Test-Path $envFile) {
        Write-Host "=== $(Get-Date -AsUTC -Format o) running client: $client ==="
        python triage.py --client $client
    }
}
