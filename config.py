"""
Per-client configuration loader.

Each client lives under clients/<name>/ with:
  - .env          secrets: GEMINI_API_KEY, SLACK_WEBHOOK_URL, connector credentials
                  (gitignored — never commit this)
  - config.json   non-secret settings: inbox type, taxonomy overrides, extra
                  prompt guidance (safe to commit — no PII, no keys)

Root .env (also gitignored) can hold OPS_SLACK_WEBHOOK_URL — the channel
*you* get paged in when a client's run fails, separate from the client's
own alert channel.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CLIENTS_DIR = ROOT / "clients"

DEFAULT_CATEGORIES = ["bug_report", "refund_request", "general_question", "urgent_complaint", "spam"]
DEFAULT_URGENCY_LEVELS = ["low", "medium", "high", "critical"]

DEFAULT_CONFIG = {
    "inbox_type": "json_file",       # json_file | gmail
    "inbox_file": "sample_inbox.json",  # json_file connector only
    "gmail_label": "INBOX",             # gmail connector only
    "categories": DEFAULT_CATEGORIES,
    "urgency_levels": DEFAULT_URGENCY_LEVELS,
    "extra_prompt_guidance": "",     # appended to the system prompt, e.g. business-specific rules
}


class ClientConfig:
    def __init__(self, name: str):
        self.name = name
        self.dir = CLIENTS_DIR / name
        if not self.dir.is_dir():
            raise FileNotFoundError(
                f"No client found at {self.dir}. Create it with a .env and config.json "
                f"(copy clients/demo/ as a starting point)."
            )

        # Root .env first (shared/ops settings), then the client's .env
        # overrides anything with the same key (e.g. its own GEMINI_API_KEY).
        load_dotenv(ROOT / ".env")
        load_dotenv(self.dir / ".env", override=True)

        data = dict(DEFAULT_CONFIG)
        cfg_path = self.dir / "config.json"
        if cfg_path.exists():
            data.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        self.data = data

        self.gemini_api_key = os.environ.get("GEMINI_API_KEY")
        self.slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
        self.ops_webhook_url = os.environ.get("OPS_SLACK_WEBHOOK_URL")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def path(self, *parts) -> Path:
        """Resolve a path inside this client's own directory."""
        return self.dir.joinpath(*parts)
