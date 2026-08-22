"""
Slack alerting.

Two separate destinations:
  - client alerts (SLACK_WEBHOOK_URL, set per-client): urgent tickets that
    need a human right now.
  - ops alerts (OPS_SLACK_WEBHOOK_URL, set in the root .env): the run
    *itself* failing — so you find out a client's bot is broken before
    they do.

Both are best-effort and use only the standard library (no extra
dependency for a single webhook POST). A Slack outage should never crash
a triage run, so failures here are caught and logged, not raised.
"""
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("triage.alerts")


def send_slack(webhook_url: str, text: str) -> bool:
    if not webhook_url:
        return False
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError) as e:
        logger.warning("Slack alert failed: %s", e)
        return False


def alert_urgent_ticket(webhook_url: str, client_name: str, ticket_id, urgency: str, summary: str):
    text = f"🚨 *[{client_name}] Urgent ticket #{ticket_id}* ({urgency.upper()})\n{summary}"
    send_slack(webhook_url, text)


def alert_ops(webhook_url: str, client_name: str, message: str):
    if not webhook_url:
        logger.warning("No OPS_SLACK_WEBHOOK_URL set — ops alert for '%s' only logged, not sent:\n%s",
                        client_name, message)
        return
    text = f"⚠️ *[{client_name}] Triage bot issue*\n```{message}```"
    send_slack(webhook_url, text)
