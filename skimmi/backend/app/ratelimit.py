"""Simple in-memory rate limits (sliding window), per user or per client address.

Limits are in config.RATE_LIMITS as name -> (requests, seconds). They are kept in
this process's memory: enough for one backend instance. Several instances would
need a shared store such as Redis (a later phase).
"""

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

from . import config

_hits = defaultdict(deque)  # (limit name, key) -> timestamps of recent hits
_lock = threading.Lock()


def _window(name, key, now):
    limit, seconds = config.RATE_LIMITS[name]
    hits = _hits[(name, key)]
    while hits and hits[0] <= now - seconds:
        hits.popleft()
    return hits, limit, seconds


def _too_many(hits, seconds, now):
    retry = max(1, math.ceil(hits[0] + seconds - now))
    raise HTTPException(
        429,
        "You're doing that too often. Wait a moment and try again.",
        headers={"Retry-After": str(retry)},
    )


def check_blocked(name, key):
    """Raise 429 if `key` has used up the limit, without counting a hit."""
    now = time.monotonic()
    with _lock:
        hits, limit, seconds = _window(name, key, now)
        if len(hits) >= limit:
            _too_many(hits, seconds, now)


def hit(name, key):
    """Count one request, raising 429 if the limit is already used up."""
    now = time.monotonic()
    with _lock:
        hits, limit, seconds = _window(name, key, now)
        if len(hits) >= limit:
            _too_many(hits, seconds, now)
        hits.append(now)


def reset():
    with _lock:
        _hits.clear()
