"""
Configuration loader.

This whole project folder is the deliverable: to onboard a new client, copy
the entire folder, fill in .env and config.json with that client's own
details, and hand them the folder to host themselves. There is no
clients/<name>/ nesting — one folder is one client, and everything it needs
lives at the project root:

  - .env          secrets: GEMINI_API_KEY, SLACK_WEBHOOK_URL,
                  OPS_SLACK_WEBHOOK_URL, connector credentials (gitignored
                  — never commit this)
  - config.json   non-secret settings: business name, inbox type, taxonomy
                  overrides, extra prompt guidance (safe to commit — no
                  PII, no keys)
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

DEFAULT_CATEGORIES = ["bug_report", "refund_request", "general_question", "urgent_complaint", "spam"]
DEFAULT_URGENCY_LEVELS = ["low", "medium", "high", "critical"]

DEFAULT_CONFIG = {
    "business_name": "",             # shown in Slack alerts and the HTML report title
    "inbox_type": "json_file",       # json_file | gmail
    "inbox_file": "sample_inbox.json",  # json_file connector only
    "gmail_label": "INBOX",             # gmail connector only
    "google_sheet_id": "",            # the ticket board; "" disables Sheets writes
    "categories": DEFAULT_CATEGORIES,
    "urgency_levels": DEFAULT_URGENCY_LEVELS,
    "extra_prompt_guidance": "",     # appended to the system prompt, e.g. business-specific rules
}


class Config:
    def __init__(self):
        load_dotenv(ROOT / ".env")

        data = dict(DEFAULT_CONFIG)
        cfg_path = ROOT / "config.json"
        if cfg_path.exists():
            data.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        self.data = data

        # Used to label Slack alerts and the HTML report title. Falls back
        # to something generic so a client copy that forgot to set it still
        # produces readable output instead of an empty "[]" prefix.
        self.name = data.get("business_name") or "Support Triage Bot"

        self.gemini_api_key = os.environ.get("GEMINI_API_KEY")
        self.slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        self.ops_webhook_url = os.environ.get("OPS_SLACK_WEBHOOK_URL")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def path(self, *parts) -> Path:
        """Resolve a path inside this project folder."""
        return ROOT.joinpath(*parts)
