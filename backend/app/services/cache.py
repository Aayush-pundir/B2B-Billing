"""Tiny read-through cache for heavy, read-only aggregates (ledger, insights).

Entries are valid until any commit happens in this process (a write may change the answer) or the
TTL passes (bounds staleness when several server workers write to the same database). A session
with uncommitted changes always bypasses the cache so it can never read an older build.
"""
from __future__ import annotations

import time
from typing import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

TTL_SECONDS = 5.0
_version = [0]
_store: dict = {}


@event.listens_for(Session, "after_commit")
def _bump(_session) -> None:
    _version[0] += 1


def cached(db: Session, name: str, build: Callable, *args):
    if db.new or db.dirty or db.deleted:
        return build(db, *args)
    key = (name, str(db.get_bind().url), args)
    hit = _store.get(key)
    now = time.monotonic()
    if hit and hit[0] == _version[0] and now - hit[1] < TTL_SECONDS:
        return hit[2]
    value = build(db, *args)
    if len(_store) > 64:
        _store.clear()
    _store[key] = (_version[0], now, value)
    return value


def clear() -> None:
    _store.clear()
