"""
Inbox connectors.

Every connector module exposes:

    fetch_messages(config: Config) -> list[dict]

returning messages shaped like:

    {"id": ..., "from_name": ..., "from_email": ..., "subject": ..., "body": ...}

`id` must be stable across runs — it's the idempotency key used in state.py,
so a given customer message must always come back with the same id.

A connector may optionally expose:

    mark_done(config: Config, message_id) -> None

as a best-effort UX nicety (e.g. marking a Gmail message read). state.db
remains the actual source of truth for "already processed" — mark_done
failing or being absent never affects correctness.

Connector-specific dependencies (e.g. the Gmail API client libraries) are
only imported when that connector is actually used, so a client running
the plain json_file connector doesn't need them installed.
"""
import importlib

CONNECTOR_MODULES = {
    "json_file": "connectors.json_file",
    "gmail": "connectors.gmail",
}


def _load(inbox_type: str):
    module_name = CONNECTOR_MODULES.get(inbox_type)
    if not module_name:
        raise ValueError(f"Unknown inbox_type '{inbox_type}'. Options: {list(CONNECTOR_MODULES)}")
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(
            f"inbox_type '{inbox_type}' needs extra dependencies that aren't installed "
            f"(pip install -r requirements-{inbox_type}.txt): {e}"
        ) from e


def fetch_messages(config) -> list:
    module = _load(config.get("inbox_type", "json_file"))
    return module.fetch_messages(config)


def mark_done(config, message_id) -> None:
    module = _load(config.get("inbox_type", "json_file"))
    fn = getattr(module, "mark_done", None)
    if fn is None:
        return
    try:
        fn(config, message_id)
    except Exception:
        pass  # best-effort only — state.db is the real idempotency source of truth
