"""Run locks: prevent internal cron and external agents from running the
same LLM pass concurrently. Manifest flags already prevent double-integration
of individual items; this prevents interleaved runs entirely.

Lock = .brain/lock.<name> containing pid + ISO timestamp. Locks older than
STALE_MINUTES are considered abandoned (crashed run) and are stolen.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from brain.config import Config

STALE_MINUTES = 120


class LockedError(RuntimeError):
    pass


@contextmanager
def run_lock(config: Config, name: str):
    path = config.state_dir / f"lock.{name}"
    now = datetime.now(timezone.utc)
    if path.exists():
        try:
            _, ts = path.read_text().strip().split(" ", 1)
            age_ok = datetime.fromisoformat(ts) > now - timedelta(minutes=STALE_MINUTES)
        except (ValueError, OSError):
            age_ok = False
        if age_ok:
            raise LockedError(
                f"another '{name}' run is in progress (lock: {path}); "
                f"remove the file if this is stale"
            )
    path.write_text(f"{os.getpid()} {now.isoformat()}")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
