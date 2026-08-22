# Support Ticket Triage Bot

Takes raw customer messages (email/form/chat) and automatically classifies,
summarizes, routes, and drafts a reply for a human to approve. Never
auto-sends anything.

Built for running one isolated instance per client (see **Adding a new
client** below) — not a shared multi-tenant service.

## Setup

```bash
cd support-triage-bot
pip install -r requirements.txt
```

Get a free Gemini API key (no credit card required) at
https://aistudio.google.com/apikey. This project uses Google's Gemini API
(`gemini-3.6-flash`) via the `google-genai` SDK.

## Run the demo client

```bash
python triage.py                      # same as --client demo
python triage.py --client demo        # processes clients/demo/sample_inbox.json (18 fake tickets)
```

Output, written under `clients/demo/`:
- `tickets_log.csv` — cumulative audit trail of every ticket ever processed for this client (category/urgency/queue/summary/draft reply), ready to open in Excel/Sheets
- `report.html` — a demo-friendly visual report of **this run's newly-processed tickets**, sorted by urgency, with draft replies inline
- `state.db` — SQLite idempotency store; re-running the bot never reprocesses or re-alerts on a ticket it already handled

## Architecture

```
triage.py        entry point — classify, route, log, alert (client-agnostic)
config.py        loads clients/<name>/.env + config.json into one object
state.py         SQLite idempotency store (one state.db per client)
lock.py          file lock so overlapping scheduled runs can't race
alerts.py        Slack webhooks — urgent tickets to the client, failures to you
connectors/      pluggable inbox sources: json_file, gmail
clients/<name>/  one directory per client — see below
```

## Adding a new client

```
clients/<name>/
  .env            secrets — GEMINI_API_KEY, SLACK_WEBHOOK_URL (gitignored)
  config.json     inbox_type, taxonomy overrides, extra prompt guidance (safe to commit)
  sample_inbox.json / credentials.json / token.json   connector-specific, as needed
```

1. `mkdir clients/<name>` and copy `clients/demo/.env.example` → `clients/<name>/.env`, filling in that client's own Gemini API key (recommended: have the client provision their own key so API cost is on their books, not yours) and their Slack alert webhook.
2. Add a `clients/<name>/config.json`. Minimum viable:
   ```json
   { "inbox_type": "json_file", "inbox_file": "sample_inbox.json" }
   ```
   Optional overrides — only set what differs from the default:
   ```json
   {
     "inbox_type": "gmail",
     "gmail_label": "Support",
     "categories": ["bug_report", "billing_dispute", "feature_request", "general_question", "spam"],
     "urgency_levels": ["low", "medium", "high", "critical"],
     "extra_prompt_guidance": "This client is a B2B SaaS company. Treat any mention of 'API down' or 'production outage' as critical urgency regardless of tone."
   }
   ```
3. Run it: `python triage.py --client <name>`.

## Connecting a real inbox

**json_file** (default) — points at a JSON file that gets appended to over
time (e.g. by a separate export job from a contact form). Fine as a
permanent option for a client without a live mailbox integration.

**gmail** — pulls unread messages from the client's own Gmail inbox (or a
specific label) via the Gmail API, using OAuth credentials scoped to that
one mailbox.

```bash
pip install -r requirements.txt -r requirements-gmail.txt
```

1. In Google Cloud Console, enable the **Gmail API** and create an OAuth
   client ID (type: **Desktop app**). Download it as
   `clients/<name>/credentials.json`.
2. Run the one-time interactive auth flow:
   ```bash
   python triage.py --client <name> --gmail-auth
   ```
   This opens a browser for consent and writes `clients/<name>/token.json`.
   Tokens refresh automatically after that — no more interactive steps,
   which is what makes it safe to run unattended from a scheduler.
3. Set `"inbox_type": "gmail"` in that client's `config.json`.

Processed messages are marked read in the mailbox as a visual confirmation,
but `state.db` — not the mailbox's read/unread flag — is the actual source
of truth for "already triaged."

To add another source (Zendesk, Intercom, a contact-form webhook), add a
new module under `connectors/` exposing the same `fetch_messages(config)`
interface — the triage logic itself never needs to change.

## Alerting

- **Client alerts** — set `SLACK_WEBHOOK_URL` in `clients/<name>/.env`.
  Every ticket routed to `urgent_queue` posts there immediately.
- **Ops alerts** — set `OPS_SLACK_WEBHOOK_URL` in the root `.env` (see
  `.env.example`). This is *your* channel: it fires if a client's run
  crashes, or if a stale lock suggests a previous run died mid-execution
  without cleaning up. This is how you find out a client's bot is down
  before they do.

## Scheduling

Nothing runs on its own — wire one of these up:

- **cron** (Linux, e.g. a small VM): `crontab -e`, then
  ```
  */10 * * * * cd /path/to/support-triage-bot && ./scripts/run_all_clients.sh >> logs/cron.log 2>&1
  ```
- **Windows Task Scheduler**: create a task that runs every 10 minutes with
  action `powershell.exe -File "C:\path\to\support-triage-bot\scripts\run_all_clients.ps1"`.
- **Cloud scheduler** (e.g. GCP Cloud Scheduler + Cloud Run Jobs, or a
  scheduled GitHub Actions workflow): call `python triage.py --client <name>`
  per client on whatever interval fits their ticket volume.

`scripts/run_all_clients.{sh,ps1}` loop over every client under `clients/`
that has a `.env`, running them one at a time; one client failing doesn't
stop the others, since `triage.py` contains its own per-run crash handling
and ops alerting.

A file lock (`clients/<name>/.run.lock`) prevents two overlapping runs for
the same client from racing on `state.db` — if a run is scheduled too
tightly and the previous one is still going, the new one just skips and
logs a warning rather than colliding.

## What it classifies

Default taxonomy (overridable per client in `config.json`):
- **category**: bug_report, refund_request, general_question, urgent_complaint, spam
- **urgency**: low, medium, high, critical

Routes to a queue (urgent_queue, billing_queue, engineering_queue,
general_queue, spam_review) and posts a 🚨 Slack alert for anything landing
in `urgent_queue`.

## Data handling

Customer message content is sent to Google's Gemini API for classification.
Say so plainly in client agreements, especially for clients in regulated
spaces (healthcare, finance) — they'll ask. `tickets_log.csv`, `report.html`,
`state.db`, and OAuth tokens all contain or grant access to real customer
data and are gitignored by default; never commit them, and treat each
client's `clients/<name>/` directory as needing the same access controls
you'd give their real inbox.

## Demo script (60-90s Loom)

1. Show `clients/demo/sample_inbox.json` — a messy pile of 18 unsorted real-looking messages.
2. Run `python triage.py` — narrate the terminal output scrolling by with instant classification.
3. Open `clients/demo/report.html` — sorted by urgency, each ticket with category, summary, and a ready-to-approve draft reply.
4. Close with: "Your team just went from reading 18 messages one by one to reviewing 18 pre-sorted, pre-drafted answers in under a minute."

For a real client pitch, swap the sample inbox for a short export of their
own recent tickets (read-only, nothing sent) — much more convincing than
fake data.
