"""
Simple file-based run lock.

Stops a scheduled run from overlapping with a still-running previous one
(e.g. a slow batch plus an aggressive cron interval) which could otherwise
race on the same state.db. Not distributed-safe — fine for the "one job
per client, one machine" setup this project targets.

If a lock file is found but is older than STALE_AFTER_SECONDS, it's
assumed to be left over from a run that crashed without cleaning up, and
this run proceeds after removing it (the caller is told via the yielded
`was_stale` flag so it can raise an ops alert).
"""
import os
import time
from contextlib import contextmanager
from pathlib import Path

STALE_AFTER_SECONDS = 30 * 60  # a single run should never legitimately take this long


class LockHeld(Exception):
    pass


@contextmanager
def run_lock(lock_path: Path):
    was_stale = False
    if lock_path.exists():
        age = time.time() - lock_path.stat().st_mtime
        if age < STALE_AFTER_SECONDS:
            raise LockHeld(f"Lock at {lock_path} held by another run ({age:.0f}s old) — skipping this run.")
        was_stale = True
        lock_path.unlink()

    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield was_stale
    finally:
        lock_path.unlink(missing_ok=True)
