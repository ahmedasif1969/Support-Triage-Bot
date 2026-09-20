# Support Ticket Triage Bot

Takes raw customer messages (email/form/chat) and automatically classifies,
summarizes, routes, and drafts a reply for a human to approve. Never
auto-sends anything.

Every ticket lands as a row in the client's own Google Sheet (their actual
ticket board — status starts "Pending," their team updates it by hand as
they work through it). Urgent tickets additionally post to Slack with a
link straight to that row.

**One folder = one client.** This whole project directory is the
deliverable: to onboard a new client, copy the entire folder, fill in
`.env` and `config.json` with their own details, and hand them the folder
to host themselves (their own server/schedule). There's no shared
multi-tenant instance and no `--client` flag — every copy is fully
independent. See **Delivering to a client** below.

## Setup

```bash
cd support-triage-bot
pip install -r requirements.txt
```

Get a free Gemini API key (no credit card required) at
https://aistudio.google.com/apikey. This project uses Google's Gemini API
(`gemini-3.6-flash`) via the `google-genai` SDK.

## Run the demo

```bash
python triage.py    # processes sample_inbox.json (18 fake tickets)
```

Output, written to the project root:
- **Google Sheet** (if `google_sheet_id` is set) — the client's real ticket board, one row per ticket, status "Pending" until their team marks it handled. This is what a client actually uses day to day.
- `tickets_log.csv` — a local cumulative audit trail (backup copy, same data as the sheet), ready to open in Excel
- `report.html` — a demo-friendly visual snapshot of **this run's newly-processed tickets** — handy for a Loom/demo, not meant as the client's ongoing interface
- `state.db` — SQLite idempotency store; re-running the bot never reprocesses or re-alerts on a ticket it already handled

The project ships with `google_sheet_id` empty, so it runs with no Google
Sheets setup required — Sheets writing is skipped entirely and you just
get the CSV/HTML output. Set `google_sheet_id` (see **The ticket board**)
once you want the full experience.

## Architecture

```
triage.py        entry point — classify, route, log, alert
config.py        loads .env + config.json into one object
state.py         SQLite idempotency store (state.db)
lock.py          file lock so overlapping scheduled runs can't race
sheets.py        writes every ticket to the client's Google Sheet (their ticket board)
alerts.py        Slack webhooks — urgent tickets to the client (with a link into
                 the sheet), run failures to an ops channel
connectors/      pluggable inbox sources: json_file, gmail
.env             secrets — GEMINI_API_KEY, SLACK_WEBHOOK_URL, etc. (gitignored)
config.json      business_name, inbox_type, taxonomy overrides, extra prompt guidance (safe to commit)
```

## Configuring this copy for a client

Edit two files in place — there's no separate directory to create:

1. Copy `.env.example` → `.env` and fill in that client's own Gemini API
   key (recommended: have the client provision their own key so API cost
   is on their books, not yours) and their Slack alert webhook.
2. Edit `config.json`. Minimum viable:
   ```json
   { "business_name": "Acme Inc", "inbox_type": "json_file", "inbox_file": "sample_inbox.json" }
   ```
   Optional overrides — only set what differs from the default:
   ```json
   {
     "business_name": "Acme Inc",
     "inbox_type": "gmail",
     "gmail_label": "Support",
     "categories": ["bug_report", "billing_dispute", "feature_request", "general_question", "spam"],
     "urgency_levels": ["low", "medium", "high", "critical"],
     "extra_prompt_guidance": "This client is a B2B SaaS company. Treat any mention of 'API down' or 'production outage' as critical urgency regardless of tone."
   }
   ```
   `business_name` labels Slack alerts and the HTML report title — set it
   to the client's actual name.
3. Run it: `python triage.py`.

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
   client ID (type: **Desktop app**). Download it as `credentials.json` in
   the project root.
2. Run the one-time interactive auth flow:
   ```bash
   python triage.py --gmail-auth
   ```
   This opens a browser for consent and writes `token.json` in the project
   root. Tokens refresh automatically after that — no more interactive
   steps, which is what makes it safe to run unattended from a scheduler.
3. Set `"inbox_type": "gmail"` in `config.json`.

Processed messages are marked read in the mailbox as a visual confirmation,
but `state.db` — not the mailbox's read/unread flag — is the actual source
of truth for "already triaged."

To add another source (Zendesk, Intercom, a contact-form webhook), add a
new module under `connectors/` exposing the same `fetch_messages(config)`
interface — the triage logic itself never needs to change.

## The ticket board (Google Sheets)

This is the client's actual day-to-day interface — a live spreadsheet
listing every ticket, its category/urgency/summary/draft reply, and a
`status` column that starts "Pending." Their team edits `status` by hand
(Pending → Sent / Dismissed) as they work through it, and edits the draft
text directly in the sheet before copying it into their own email client
to send — the bot never sends anything itself.

Setup, per client:
1. In Google Cloud Console, create a **service account** and download its
   JSON key. Save it as `sheets_credentials.json` in the project root.
2. Create a blank Google Sheet. Share it (**Editor** access) with the
   service account's email address — it's inside the downloaded key file
   as `"client_email"`, looks like `...@...iam.gserviceaccount.com`.
3. Copy the sheet's ID out of its URL —
   `https://docs.google.com/spreadsheets/d/<THIS PART>/edit` — into
   `"google_sheet_id"` in `config.json`.

The header row is created automatically on first run. Leave
`google_sheet_id` empty (the default) to skip Sheets entirely and fall
back to just the local CSV/HTML output — useful for testing without a
Google Cloud setup.

## Alerting

- **Client alerts (Slack)** — set `SLACK_WEBHOOK_URL` in `.env`. Only
  tickets routed to `urgent_queue` post here — everything else (routine
  bug reports, refund requests, general questions) only needs to show up
  in the sheet, not interrupt anyone. Each urgent alert includes a link
  straight to that ticket's row in the sheet, so clicking it jumps
  directly to the draft that needs a response.
- **Ops alerts** — set `OPS_SLACK_WEBHOOK_URL` in `.env` (see
  `.env.example`). This fires if a run crashes, or if a stale lock
  suggests a previous run died mid-execution without cleaning up. Point
  this at whoever is actually on call for that deployment — if you're
  hosting it yourself for a client, that's you; if you've delivered a
  self-hosted copy, that's the client's own team (or you, only if you're
  selling an ongoing support retainer on top).

## Scheduling

Nothing runs on its own — wire one of these up:

- **cron** (Linux, e.g. a small VM): `crontab -e`, then
  ```
  */10 * * * * cd /path/to/support-triage-bot && ./scripts/run.sh >> logs/cron.log 2>&1
  ```
- **Windows Task Scheduler**: create a task that runs every 10 minutes with
  action `powershell.exe -File "C:\path\to\support-triage-bot\scripts\run.ps1"`.
- **Cloud scheduler** (e.g. GCP Cloud Scheduler + Cloud Run Jobs, or a
  scheduled GitHub Actions workflow): call `python triage.py` on whatever
  interval fits the client's ticket volume.

A file lock (`.run.lock`) prevents two overlapping runs from racing on
`state.db` — if a run is scheduled too tightly and the previous one is
still going, the new one just skips and logs a warning rather than
colliding.

## Delivering to a client (self-hosted handoff)

Each client gets their own full copy of this project — not a subfolder or
a shared instance on your server. To onboard a new client:

1. Duplicate this whole project directory (e.g.
   `cp -r support-triage-bot support-triage-bot-acme`).
2. Inside the copy, follow **Configuring this copy for a client** above —
   `business_name`, taxonomy, inbox type, all of it lives in that copy's
   own `.env` and `config.json`.
3. Fill in their own Gemini API key, Slack webhook, and Sheets/Gmail
   credentials — everything in that copy should be *theirs*, not yours.
4. Hand over the whole folder (a zip, or a private git repo with
   ownership transferred to them) along with this README, and either walk
   them through hosting it (a small always-on machine + the scheduling
   steps above) or do that initial setup for them as part of delivery.

Because each client runs an independent copy, a bug fix or prompt
improvement you make later doesn't automatically reach clients you've
already delivered to — you'd need to re-deliver the update (re-copy your
changed files into their folder, or `git merge`/`git cherry-pick` if their
copy is a fork of your repo), or agree on an ongoing support arrangement
if you want to keep pushing improvements to them.

## What it classifies

Default taxonomy (overridable in `config.json`):
- **category**: bug_report, refund_request, general_question, urgent_complaint, spam
- **urgency**: low, medium, high, critical

Routes to a queue (urgent_queue, billing_queue, engineering_queue,
general_queue, spam_review). Every ticket lands in the Sheet regardless of
queue; only `urgent_queue` additionally posts a 🚨 Slack alert.

## Data handling

Customer message content is sent to Google's Gemini API for classification,
and every ticket (including full draft replies) is written to the client's
Google Sheet — say so plainly in client agreements, especially for clients
in regulated spaces (healthcare, finance) — they'll ask. `tickets_log.csv`,
`report.html`, `state.db`, `sheets_credentials.json`, and OAuth tokens all
contain or grant access to real customer data and are gitignored by
default; never commit them, and treat each client's copy of this project
as needing the same access controls you'd give their real inbox.

## Demo script (60-90s Loom)

1. Show `sample_inbox.json` — a messy pile of 18 unsorted real-looking messages.
2. Run `python triage.py` — narrate the terminal output scrolling by with instant classification.
3. Open `report.html` — sorted by urgency, each ticket with category, summary, and a ready-to-approve draft reply.
4. Close with: "Your team just went from reading 18 messages one by one to reviewing 18 pre-sorted, pre-drafted answers in under a minute."

For a real client pitch, swap the sample inbox for a short export of their
own recent tickets (read-only, nothing sent) — much more convincing than
fake data.
