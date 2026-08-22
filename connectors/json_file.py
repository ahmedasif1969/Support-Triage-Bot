"""
Reads a static JSON file as the "inbox" — the original demo/testing
behavior, and a fine permanent option for a client whose messages arrive
as periodic exports rather than a live mailbox.

Every message in the file is returned on every run; state.py's idempotency
check is what stops already-processed ones from being re-triaged, so it's
safe to point this at a file that's appended to over time (e.g. by another
process exporting new form submissions).
"""
import json


def fetch_messages(config) -> list:
    inbox_file = config.path(config.get("inbox_file", "sample_inbox.json"))
    with open(inbox_file, "r", encoding="utf-8") as f:
        return json.load(f)
