# Runs the triage bot once for this deployed copy of the project.
# triage.py has its own crash handling and ops alerting, so a failed run
# doesn't need special handling here.
#
# Windows Task Scheduler example: trigger every 10 minutes, action:
#   powershell.exe -File "C:\path\to\support-triage-bot\scripts\run.ps1"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "logs" | Out-Null

python triage.py
