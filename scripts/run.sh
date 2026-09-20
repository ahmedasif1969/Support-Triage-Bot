#!/usr/bin/env bash
# Runs the triage bot once for this deployed copy of the project.
# triage.py has its own crash handling and ops alerting, so a failed run
# doesn't need special handling here.
#
# Cron example (every 10 minutes), from the project root:
#   */10 * * * * cd /path/to/support-triage-bot && ./scripts/run.sh >> logs/cron.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

python triage.py
