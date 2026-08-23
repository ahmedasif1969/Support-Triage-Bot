"""
Google Sheets integration — this is the client's actual ticket board.

Every classified, non-spam-only-CSV ticket gets appended as a row (id,
timestamp, category, urgency, queue, summary, draft reply, status).
"status" starts as "Pending" and is meant to be edited by hand by the
client's team as they work through tickets (Pending -> Sent / Dismissed).
This module only ever appends new rows — it never reads status back — so
there's no risk of the bot fighting a human over the sheet, and no
write-conflict handling needed.

Auth uses a Google service account, not interactive OAuth, because this
runs unattended on a schedule with no one available to click "allow."

One-time setup per client:
  1. In Google Cloud Console, create a service account and download its
     JSON key. Save it as clients/<name>/sheets_credentials.json.
  2. Create a blank Google Sheet. Share it (Editor access) with the
     service account's email address — it looks like
     something@your-project.iam.gserviceaccount.com and is inside the
     downloaded key file as "client_email".
  3. Copy the sheet's ID out of its URL:
     https://docs.google.com/spreadsheets/d/<THIS PART>/edit
     and set it as "google_sheet_id" in clients/<name>/config.json.

The header row is created automatically on first run if the sheet is
empty — no manual template to keep in sync.
"""
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "id", "timestamp", "from_name", "from_email", "subject", "category",
    "urgency", "queue", "customer_name", "summary", "draft_reply", "status",
]


def _open_worksheet(config):
    creds_path = config.path("sheets_credentials.json")
    if not creds_path.exists():
        raise FileNotFoundError(
            f"{creds_path} not found. Create a Google service account, download its "
            f"JSON key there, and share your Google Sheet with its email address."
        )
    sheet_id = config.get("google_sheet_id")
    if not sheet_id:
        raise ValueError(f'Set "google_sheet_id" in clients/{config.name}/config.json first.')

    creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def _ensure_header(worksheet):
    if worksheet.row_values(1) != HEADERS:
        worksheet.update("A1", [HEADERS])
        worksheet.format("A1:L1", {"textFormat": {"bold": True}})


def append_ticket(config, row: dict) -> str:
    """Appends one ticket as a new row and returns a URL that deep-links
    straight to that row, for use in the Slack alert."""
    worksheet = _open_worksheet(config)
    _ensure_header(worksheet)

    values = [
        row["id"], row["timestamp"], row["from_name"], row["from_email"],
        row["subject"], row["category"], row["urgency"], row["queue"],
        row["customer_name"], row["summary"], row["draft_reply"], "Pending",
    ]
    # Column A's filled-row count + 1 is the next empty row. Safe from races
    # because lock.py already guarantees only one run per client at a time.
    next_row = len(worksheet.col_values(1)) + 1
    worksheet.update(f"A{next_row}", [values])

    return f"https://docs.google.com/spreadsheets/d/{config.get('google_sheet_id')}/edit#gid={worksheet.id}&range=A{next_row}"
