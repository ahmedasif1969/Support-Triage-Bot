"""
Gmail connector — pulls unread messages from a given label via the Gmail
API, scoped to one client's mailbox via their own OAuth credentials.

Needs: pip install -r requirements-gmail.txt

One-time setup per client:
  1. In Google Cloud Console, create/select a project, enable the "Gmail
     API", then create an OAuth client ID of type "Desktop app". Download
     it and save it as clients/<name>/credentials.json.
  2. Run this once, interactively, to complete the OAuth consent screen:
         python triage.py --client <name> --gmail-auth
     It opens a browser, and on success writes clients/<name>/token.json.
     Tokens refresh automatically after that — no more interactive steps,
     which is what makes this safe to run from cron/a scheduler.
  3. In clients/<name>/config.json, set "inbox_type": "gmail" and
     optionally "gmail_label" (default: the main inbox).

credentials.json and token.json both grant mailbox access and must never
be committed — they're covered by .gitignore already.
"""
import base64
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _get_credentials(config):
    token_path = config.path("token.json")
    creds_path = config.path("credentials.json")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"{creds_path} not found. Download an OAuth Desktop client ID from "
                    f"Google Cloud Console and save it there, then run "
                    f"`python triage.py --client {config.name} --gmail-auth`."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def authorize_interactive(config):
    """The one-time interactive OAuth flow, run via `--gmail-auth`."""
    _get_credentials(config)
    print(f"Gmail authorized for client '{config.name}'. Token saved to {config.path('token.json')}.")


def _extract_body(payload) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    # Last resort: whatever body data exists (e.g. text/html, unparsed).
    data = payload.get("body", {}).get("data")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return ""


def fetch_messages(config) -> list:
    creds = _get_credentials(config)
    service = build("gmail", "v1", credentials=creds)

    label = config.get("gmail_label", "INBOX")
    query = "is:unread in:inbox" if label == "INBOX" else f"label:{label} is:unread"

    results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    refs = results.get("messages", [])

    messages = []
    for ref in refs:
        msg = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        name, email = parseaddr(headers.get("from", ""))
        messages.append({
            "id": msg["id"],
            "from_name": name or email or "Unknown",
            "from_email": email or "unknown",
            "subject": headers.get("subject", ""),
            "body": _extract_body(msg["payload"]),
        })
    return messages


def mark_done(config, message_id: str):
    """Marks the message read, purely as a visual confirmation in the
    mailbox — state.db is the real idempotency source of truth, so a
    failure here (permissions, transient API error) is non-fatal."""
    creds = _get_credentials(config)
    service = build("gmail", "v1", credentials=creds)
    service.users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()
