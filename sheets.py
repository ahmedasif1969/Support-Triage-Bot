"""
Google Sheets integration — this is the client's actual ticket board.

Every classified ticket gets appended as a row (id, timestamp, category,
urgency, queue, summary, draft reply, status, and a one-click Gmail reply
link). "status" starts as "Pending" and is meant to be edited by hand by
the client's team as they work through tickets (Pending -> Sent / Dismissed).
This module only ever appends new rows — it never reads status back — so
there's no risk of the bot fighting a human over the sheet, and no
write-conflict handling needed.

The "📧 Reply in Gmail" column contains a HYPERLINK formula that opens
Gmail's compose window with To, Subject, and the AI draft body pre-filled —
the worker just reviews and clicks Send.

Auth uses a Google service account, not interactive OAuth, because this
runs unattended on a schedule with no one available to click "allow."

One-time setup per client (do this once per copy of the project folder):
  1. In Google Cloud Console, create a service account and download its
     JSON key. Save it as sheets_credentials.json in the project root.
  2. Create a blank Google Sheet. Share it (Editor access) with the
     service account's email address — it looks like
     something@your-project.iam.gserviceaccount.com and is inside the
     downloaded key file as "client_email".
  3. Copy the sheet's ID out of its URL:
     https://docs.google.com/spreadsheets/d/<THIS PART>/edit
     and set it as "google_sheet_id" in config.json.

The header row is created automatically on first run if the sheet is
empty — no manual template to keep in sync.
"""
import json
import os
import time
import urllib.parse

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "id", "timestamp", "from_name", "from_email", "subject", "category",
    "urgency", "queue", "customer_name", "summary", "draft_reply", "status",
    "reply_link",
]

# Cache which sheet IDs have already had their header confirmed this process
# lifetime — avoids one extra read API call per ticket.
_header_confirmed: set = set()


def _gmail_compose_url(to_email: str, subject: str, body: str) -> str:
    """Build a Gmail compose URL with To, Subject, and Body pre-filled."""
    subject_line = (
        f"Re: {subject}"
        if not subject.lower().startswith("re:")
        else subject
    )
    safe_to = urllib.parse.quote(to_email or "", safe="")
    safe_su = urllib.parse.quote(subject_line or "", safe="")
    safe_body = urllib.parse.quote(body or "", safe="")
    
    return f"https://mail.google.com/mail/?view=cm&fs=1&to={safe_to}&su={safe_su}&body={safe_body}"


def _open_worksheet(config):
    sheet_id = config.get("google_sheet_id")
    if not sheet_id:
        raise ValueError('Set "google_sheet_id" in config.json first.')

    creds = None
    # 1. Check if full JSON is passed via environment variable (e.g. Railway Secret)
    sa_env = os.environ.get("SHEETS_SERVICE_ACCOUNT")
    if sa_env:
        try:
            info = json.loads(sa_env)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            raise ValueError(f"Failed to parse SHEETS_SERVICE_ACCOUNT environment variable: {e}")
    else:
        # 2. Fallback to local file
        creds_path = config.path("sheets_credentials.json")
        if creds_path.exists():
            creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
        else:
            raise FileNotFoundError(
                f"Sheets credentials not found. Either set SHEETS_SERVICE_ACCOUNT env var "
                f"or save service account JSON as {creds_path}."
            )

    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def _ensure_header(worksheet, sheet_id: str):
    """Write the header row if the sheet is empty.

    Cached per-process so we only ever make this read once regardless of how
    many tickets are processed in a single run — avoids burning read quota.
    """
    if sheet_id in _header_confirmed:
        return
    if worksheet.acell("A1").value != HEADERS[0]:
        worksheet.update("A1", [HEADERS])
        worksheet.format(f"A1:{chr(ord('A') + len(HEADERS) - 1)}1",
                         {"textFormat": {"bold": True}})
    _header_confirmed.add(sheet_id)


def _append_with_retry(worksheet, values: list, max_retries: int = 3):
    """Append a row using the atomic append endpoint (no reads required).

    Retries up to max_retries times on 429 / quota errors with exponential
    backoff. Raises on any other error or after exhausting retries.
    """
    backoff = [5, 15, 30]
    for attempt in range(max_retries + 1):
        try:
            # USER_ENTERED so the =HYPERLINK() formula in reply_link is
            # interpreted as a formula rather than stored as literal text.
            return worksheet.append_row(values, value_input_option="USER_ENTERED")
        except APIError as e:
            if "429" in str(e) and attempt < max_retries:
                wait = backoff[attempt]
                time.sleep(wait)
                continue
            raise


def append_ticket(config, row: dict) -> str:
    """Appends one ticket as a new row and returns the sheet URL.

    The last column contains a =HYPERLINK() formula that opens Gmail's
    compose window with To, Subject, and the AI draft body pre-filled —
    the worker just reviews and clicks Send.
    """
    sheet_id = config.get("google_sheet_id")
    worksheet = _open_worksheet(config)
    _ensure_header(worksheet, sheet_id)

    # Build the Gmail compose URL — URL-encode everything so special
    # characters in the draft body don't break the link.
    compose_url = _gmail_compose_url(
        to_email=row.get("from_email", ""),
        subject=row.get("subject", ""),
        body=row.get("draft_reply", ""),
    )
    # Escape any double-quotes in the URL so the HYPERLINK formula stays valid.
    safe_url = compose_url.replace('"', '""')
    reply_formula = f'=HYPERLINK("{safe_url}", "📧 Reply in Gmail")'

    values = [
        row["id"], row["timestamp"], row["from_name"], row["from_email"],
        row["subject"], row["category"], row["urgency"], row["queue"],
        row["customer_name"], row["summary"], row["draft_reply"], "Pending",
        reply_formula,
    ]

    _append_with_retry(worksheet, values)

    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/edit#gid={worksheet.id}"
    )
