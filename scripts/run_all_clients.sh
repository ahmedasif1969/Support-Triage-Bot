#!/usr/bin/env bash
# Runs the triage bot for every client under clients/ that has a .env file.
# One client failing (bad API key, connector error, etc.) does not stop the
# others — triage.py contains its own crash handling and ops alerting per
# client, and set -e is deliberately NOT used here so this loop continues.
#
# Cron example (every 10 minutes), from the project root:
#   */10 * * * * cd /path/to/support-triage-bot && ./scripts/run_all_clients.sh >> logs/cron.log 2>&1
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

for client_dir in clients/*/; do
    client="$(basename "$client_dir")"
    [ -f "${client_dir}.env" ] || continue
    echo "=== $(date -u +%FT%TZ) running client: ${client} ==="
    python triage.py --client "${client}"
done
