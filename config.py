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

        # Pre-seed token.json if provided via GMAIL_TOKEN_JSON env var (for Railway/headless)
        token_env = os.environ.get("GMAIL_TOKEN_JSON")
        token_file = self.path("token.json")
        if token_env and not token_file.exists():
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(token_env.strip(), encoding="utf-8")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def path(self, *parts) -> Path:
        """Resolve a path inside this project folder or respect env var overrides."""
        if len(parts) == 1:
            name = parts[0]
            if name == "token.json" and os.environ.get("GMAIL_TOKEN_PATH"):
                return Path(os.environ["GMAIL_TOKEN_PATH"]).resolve()
            if name == "state.db" and os.environ.get("STATE_DB_PATH"):
                return Path(os.environ["STATE_DB_PATH"]).resolve()
            if name == "tickets_log.csv" and (os.environ.get("TICKETS_LOG_PATH") or os.environ.get("TICKETS_LOG")):
                return Path(os.environ.get("TICKETS_LOG_PATH") or os.environ.get("TICKETS_LOG")).resolve()

        return ROOT.joinpath(*parts)
