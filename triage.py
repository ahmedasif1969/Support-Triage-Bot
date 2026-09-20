"""
Support Ticket Triage Bot
--------------------------
Fetches a batch of raw customer messages (via a pluggable connector — a
JSON file, a live Gmail inbox, etc.), classifies each new one with Gemini,
and writes a triage log (CSV, cumulative) plus a demo-ready HTML report of
what's new this run. Never sends anything to the customer automatically —
every draft reply is meant for a human to review and approve.

Single-tenant-per-folder design: this whole project directory is the
deliverable. To onboard a new client, copy the entire folder, fill in
.env and config.json with their own details, and hand them the folder to
host themselves (their own server/schedule). Everything lives at the
project root — there is no clients/<name>/ nesting:
  - .env / config.json        secrets and settings (see config.py).
  - state.py                  SQLite idempotency store so re-runs / crashed
                               runs / overlapping schedules never
                               double-process or double-alert a ticket.
  - lock.py                   file lock preventing two overlapping runs
                               from racing on state.db.
  - sheets.py                  the client's actual ticket board — every
                               ticket is appended as a row in their Google
                               Sheet with a "Pending" status they update by
                               hand. Optional (google_sheet_id).
  - alerts.py                 real Slack webhooks: urgent tickets go to the
                               client's channel (with a deep link straight
                               to that ticket's row in the sheet), run
                               failures go to an ops channel.
  - connectors/                swappable inbox sources (json_file, gmail).

Usage:
    python triage.py               # process new messages, write log/report/sheet
    python triage.py --gmail-auth  # one-time Gmail OAuth (only if inbox_type is "gmail")
"""

import argparse
import csv
import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

import alerts
import connectors
import lock
import sheets
import state
from config import Config

MODEL = "gemini-3.5-flash-lite"

SYSTEM_PROMPT = """You are a support ticket triage assistant for a small/medium business. \
You will be given one raw customer message (from email, a contact form, or chat). \
Analyze it and return a structured JSON object with these fields:

- category: one of "bug_report", "refund_request", "general_question", "urgent_complaint", "spam"
- urgency: one of "low", "medium", "high", "critical"
- customer_name: best-guess customer name as a string, or "Unknown" if not determinable
- summary: a single-sentence plain-English summary of the issue (max ~25 words)
- draft_reply: a warm, professional, ready-to-send draft reply from the support team \
addressing the customer's specific issue. For spam, draft_reply should be an empty string. \
Keep it concise (3-6 sentences), acknowledge the specific issue, and where appropriate say \
what happens next (e.g. "our team will look into this and follow up within 24 hours"). \
Never invent order numbers, refund amounts, or policies you weren't given — reference only \
details actually present in the message.

Classification guidance:
- urgent_complaint: angry/escalated tone, threats to cancel/churn, repeated unresolved issues, \
  security/privacy/data exposure, service outages affecting the customer's business.
- bug_report: something is broken or not working as expected, no explicit refund ask.
- refund_request: customer is asking for money back or disputes a charge.
- general_question: pre-sales questions, feature questions, "how do I", feedback/thanks with no issue.
- spam: promotional/phishing/irrelevant content unrelated to a real support issue.
- urgency "critical": active data/security exposure, outage affecting business operations, \
  or an extremely escalated multi-contact complaint.
- urgency "high": broken core functionality, billing errors, blocked from using the product.
- urgency "medium": real issue but not blocking, first-time question tied to an active problem.
- urgency "low": general questions, feature requests, thanks/feedback, spam.
"""


def classify_message(client: genai.Client, message: dict, categories: list, urgency_levels: list,
                      system_prompt: str) -> dict:
    response_schema = {
        "type": "OBJECT",
        "properties": {
            "category": {"type": "STRING", "enum": categories},
            "urgency": {"type": "STRING", "enum": urgency_levels},
            "customer_name": {"type": "STRING"},
            "summary": {"type": "STRING"},
            "draft_reply": {"type": "STRING"},
        },
        "required": ["category", "urgency", "customer_name", "summary", "draft_reply"],
    }

    user_content = (
        f"From: {message.get('from_name', 'Unknown')} <{message.get('from_email', 'unknown')}>\n"
        f"Subject: {message.get('subject', '')}\n\n"
        f"{message.get('body', '')}"
    )

    resp = client.models.generate_content(
        model=MODEL,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=response_schema,
        ),
    )

    if not resp.text:
        raise ValueError(f"Empty response from model (finish_reason may indicate why): {resp}")

    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\nRaw output: {resp.text[:500]}")

    # Validate/normalize against this client's taxonomy.
    default_category = "general_question" if "general_question" in categories else categories[0]
    default_urgency = "low" if "low" in urgency_levels else urgency_levels[0]
    data["category"] = data.get("category") if data.get("category") in categories else default_category
    data["urgency"] = data.get("urgency") if data.get("urgency") in urgency_levels else default_urgency
    data.setdefault("customer_name", message.get("from_name", "Unknown"))
    data.setdefault("summary", "")
    data.setdefault("draft_reply", "")
    return data


def route(_message: dict, result: dict) -> str:
    """Decide the destination queue. Returns a queue name for logging/alerting.

    `_message` is currently unused but kept in the signature so routing can later
    factor in message content (e.g. a VIP customer list, account tier lookups).
    """
    if result["category"] == "spam":
        return "spam_review"
    if result["urgency"] in ("critical", "high") or result["category"] == "urgent_complaint":
        return "urgent_queue"
    if result["category"] == "refund_request":
        return "billing_queue"
    if result["category"] == "bug_report":
        return "engineering_queue"
    return "general_queue"


def write_log(config: Config, rows: list):
    """Appends to a cumulative, per-client CSV — the full audit trail across
    every run, not just this one (each run only sees newly-fetched, not-yet-
    processed messages, so append is correct here)."""
    fieldnames = [
        "id", "timestamp", "from_name", "from_email", "subject", "category",
        "urgency", "queue", "customer_name", "summary", "draft_reply",
    ]
    log_path = config.path("tickets_log.csv")
    write_header = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_html_report(config: Config, rows: list):
    """Overwrites report.html with just *this run's* newly-triaged tickets —
    a "what's new since last check" view. Full history lives in
    tickets_log.csv."""
    urgency_color = {"critical": "#dc2626", "high": "#ea580c", "medium": "#ca8a04", "low": "#16a34a"}
    category_label = {
        "bug_report": "Bug Report", "refund_request": "Refund Request",
        "general_question": "General Question", "urgent_complaint": "Urgent Complaint",
        "spam": "Spam",
    }

    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    sort_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows_sorted = sorted(rows, key=lambda r: sort_order.get(r["urgency"], 9))

    cards = []
    for r in rows_sorted:
        color = urgency_color.get(r["urgency"], "#6b7280")
        cards.append(f"""
        <div class="ticket">
          <div class="ticket-header">
            <span class="badge" style="background:{color}22;color:{color};border:1px solid {color}55">
              {esc(r['urgency'].upper())}
            </span>
            <span class="badge category">{esc(category_label.get(r['category'], r['category']))}</span>
            <span class="queue">→ {esc(r['queue'].replace('_', ' '))}</span>
          </div>
          <div class="ticket-subject">#{esc(r['id'])} · {esc(r['subject'])}</div>
          <div class="ticket-from">{esc(r['customer_name'])} &lt;{esc(r['from_email'])}&gt;</div>
          <div class="ticket-summary">{esc(r['summary'])}</div>
          {'<div class="draft"><div class="draft-label">Suggested reply (needs approval)</div>' + esc(r['draft_reply']) + '</div>' if r['draft_reply'] else ''}
        </div>""")

    html = f"""<title>Support Triage Report — {esc(config.name)}</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #111827; --muted: #6b7280; --card-bg: #f9fafb; --border: #e5e7eb;
    --accent: #4f46e5;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --card-bg: #1a1d24; --border: #2a2e37;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --card-bg: #1a1d24; --border: #2a2e37;
  }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, Segoe UI, sans-serif;
          max-width: 860px; margin: 0 auto; padding: 32px 20px 80px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 28px; font-size: 0.95rem; }}
  .stats {{ display: flex; gap: 12px; margin-bottom: 28px; flex-wrap: wrap; }}
  .stat {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
           padding: 10px 16px; font-size: 0.85rem; color: var(--muted); }}
  .stat b {{ color: var(--fg); font-size: 1.1rem; display: block; }}
  .ticket {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
             padding: 16px 18px; margin-bottom: 14px; }}
  .ticket-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }}
  .badge {{ font-size: 0.7rem; font-weight: 700; letter-spacing: 0.03em; padding: 3px 8px; border-radius: 6px; }}
  .badge.category {{ background: var(--border); color: var(--muted); border: 1px solid var(--border); }}
  .queue {{ font-size: 0.78rem; color: var(--muted); margin-left: auto; }}
  .ticket-subject {{ font-weight: 600; font-size: 1rem; margin-bottom: 2px; }}
  .ticket-from {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 8px; }}
  .ticket-summary {{ font-size: 0.9rem; margin-bottom: 10px; }}
  .draft {{ background: var(--bg); border: 1px dashed var(--border); border-radius: 8px;
            padding: 10px 12px; font-size: 0.85rem; white-space: pre-wrap; }}
  .draft-label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em;
                  color: var(--accent); font-weight: 700; margin-bottom: 6px; }}
</style>
<h1>Support Triage Report — {esc(config.name)}</h1>
<div class="subtitle">Generated {esc(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))} ·
  {len(rows)} new ticket(s) this run · full history in tickets_log.csv</div>
<div class="stats">
  <div class="stat"><b>{sum(1 for r in rows if r['queue']=='urgent_queue')}</b>Urgent</div>
  <div class="stat"><b>{sum(1 for r in rows if r['category']=='refund_request')}</b>Refund requests</div>
  <div class="stat"><b>{sum(1 for r in rows if r['category']=='bug_report')}</b>Bug reports</div>
  <div class="stat"><b>{sum(1 for r in rows if r['category']=='spam')}</b>Spam filtered</div>
</div>
{''.join(cards)}
"""
    with open(config.path("report.html"), "w", encoding="utf-8") as f:
        f.write(html)


def _run(config: Config, logger: logging.Logger):
    client = genai.Client(api_key=config.gemini_api_key)

    categories = config.get("categories")
    urgency_levels = config.get("urgency_levels")
    system_prompt = SYSTEM_PROMPT
    extra_guidance = config.get("extra_prompt_guidance")
    if extra_guidance:
        system_prompt = system_prompt + "\n\nClient-specific guidance:\n" + extra_guidance

    logger.info("Fetching messages for '%s' (inbox_type=%s)...", config.name, config.get("inbox_type"))
    messages = connectors.fetch_messages(config)
    logger.info("Fetched %d message(s).", len(messages))

    conn = state.connect(config.path("state.db"))

    new_rows = []
    skipped = 0
    for msg in messages:
        msg_id = msg.get("id")
        if msg_id is None:
            logger.warning("Skipping a message with no id: %r", msg)
            continue
        if state.is_processed(conn, msg_id):
            skipped += 1
            continue

        result = None
        backoff = [5, 15, 30]  # seconds; retries capacity (503) and rate-limit (429) errors
        for attempt in range(len(backoff) + 1):
            try:
                result = classify_message(client, msg, categories, urgency_levels, system_prompt)
                break
            except genai_errors.ClientError as e:
                if getattr(e, "code", None) == 429 and attempt < len(backoff):
                    wait = backoff[attempt]
                    logger.warning("Rate limited on ticket #%s, waiting %ds (attempt %d)...", msg_id, wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.warning("Ticket #%s failed to classify: %s", msg_id, e)
                break
            except genai_errors.ServerError as e:
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    logger.warning("Model unavailable for ticket #%s, waiting %ds (attempt %d)...", msg_id, wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.warning("Ticket #%s failed to classify after retries: %s", msg_id, e)
                break
            except ValueError as e:
                logger.warning("Ticket #%s failed to classify: %s", msg_id, e)
                break
            except Exception as e:
                # Catches anything not already handled above — network
                # timeouts, DNS blips, connection resets, etc. These are
                # transient and retryable exactly like a 503, but they
                # don't come back as a genai_errors type, so without this
                # they'd propagate uncaught and kill the *entire* batch
                # over one bad connection instead of just this ticket.
                if attempt < len(backoff):
                    wait = backoff[attempt]
                    logger.warning("Unexpected error on ticket #%s (%s), waiting %ds (attempt %d)...",
                                    msg_id, e, wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.warning("Ticket #%s failed to classify after retries: %s", msg_id, e)
                break

        if result is None:
            result = {"category": "general_question", "urgency": "low",
                      "customer_name": msg.get("from_name", "Unknown"),
                      "summary": "(classification failed — needs manual review)",
                      "draft_reply": ""}

        queue = route(msg, result)

        row = {
            "id": msg_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_name": msg.get("from_name", ""),
            "from_email": msg.get("from_email", ""),
            "subject": msg.get("subject", ""),
            "category": result["category"],
            "urgency": result["urgency"],
            "queue": queue,
            "customer_name": result["customer_name"],
            "summary": result["summary"],
            "draft_reply": result["draft_reply"],
        }
        new_rows.append(row)

        # The Sheet is the client's actual ticket board — every ticket goes
        # there (not just urgent ones) so their team has one place to see
        # everything and mark it handled. Optional: a client with no
        # google_sheet_id configured just skips this silently.
        sheet_link = None
        if config.get("google_sheet_id"):
            try:
                sheet_link = sheets.append_ticket(config, row)
            except Exception as e:
                logger.warning("Failed to write ticket #%s to the Google Sheet: %s", msg_id, e)

        if queue == "urgent_queue":
            print(f"🚨 URGENT ALERT — Ticket #{msg_id} ({result['urgency'].upper()}): {result['summary']}")
            alerts.alert_urgent_ticket(config.slack_webhook_url, config.name, msg_id,
                                        result["urgency"], result["summary"], sheet_link)

        # Mark processed BEFORE the mailbox side-effect: state.db is the
        # source of truth, so a crash between these two lines just means
        # mark_done() (a cosmetic "mark as read") gets retried harmlessly,
        # never a re-classification.
        state.mark_processed(conn, msg_id, row["timestamp"], result["category"], result["urgency"], queue)
        connectors.mark_done(config, msg_id)

        print(f"#{str(msg_id):>4} [{result['urgency']:>8}] {result['category']:<18} -> {queue:<16} "
              f"| {msg.get('subject', '')[:45]}")

        time.sleep(0.5)  # gentle pacing for free-tier rate limits

    conn.close()

    if skipped:
        logger.info("Skipped %d already-processed message(s).", skipped)

    if new_rows:
        write_log(config, new_rows)
        write_html_report(config, new_rows)
        logger.info("Wrote %s and %s.", config.path("tickets_log.csv"), config.path("report.html"))
    else:
        logger.info("No new messages to process.")

    urgent_count = sum(1 for r in new_rows if r["queue"] == "urgent_queue")
    if urgent_count:
        logger.info("%d ticket(s) need urgent human attention.", urgent_count)


def main():
    parser = argparse.ArgumentParser(description="Support ticket triage bot")
    parser.add_argument("--gmail-auth", action="store_true",
                         help="Run the one-time interactive Gmail OAuth flow, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("triage")

    config = Config()

    if args.gmail_auth:
        from connectors import gmail
        gmail.authorize_interactive(config)
        return

    if not config.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set. Add it to .env (see .env.example).")
        sys.exit(1)

    lock_path = config.path(".run.lock")
    try:
        with lock.run_lock(lock_path) as was_stale:
            if was_stale:
                logger.warning("Previous lock was stale — a prior run may have crashed "
                                "without cleaning up.")
                alerts.alert_ops(config.ops_webhook_url, config.name,
                                  "Previous run's lock file was stale (crashed without cleanup?). "
                                  "This run proceeded anyway.")
            _run(config, logger)
    except lock.LockHeld as e:
        logger.warning(str(e))
    except Exception:
        tb = traceback.format_exc()
        logger.error("Run failed:\n%s", tb)
        alerts.alert_ops(config.ops_webhook_url, config.name, tb[-1500:])
        sys.exit(1)


if __name__ == "__main__":
    main()
