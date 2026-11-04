"""Short-lived, process-local snapshots for the authorized requests listing."""
import base64
import binascii
import hashlib
import json
import threading
import time

from webapp.services import solicitudes_repository as repository

TTL_SECONDS = 300
_BUCKETS = [(threading.Lock(), {}) for _ in range(8)]
_BUCKET_LIMIT = 8


def _expiration(token):
    # Claims only limit reuse. A successful Graph read establishes access.
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        exp = claims["exp"]
        return exp if isinstance(exp, int) and not isinstance(exp, bool) else 0
    except (IndexError, KeyError, ValueError, TypeError, binascii.Error):
        return 0


def read(actor, token, *, refresh=False):
    expiration = _expiration(token)
    if not actor or expiration <= time.time():
        return repository.read_requests(token)
    key = actor, hashlib.sha256(token.encode()).digest()
    lock, entries = _BUCKETS[hash(actor) % len(_BUCKETS)]
    with lock:
        now = time.monotonic()
        for expired in [key for key, (deadline, _) in entries.items() if deadline <= now]:
            del entries[expired]
        if refresh:
            entries.pop(key, None)
        cached = entries.get(key)
        if cached and expiration > time.time():
            return cached[1]
        rows = repository.read_requests(token)
        remaining = min(TTL_SECONDS, expiration - time.time())
        if remaining > 0:
            if len(entries) >= _BUCKET_LIMIT:
                del entries[min(entries, key=lambda key: entries[key][0])]
            entries[key] = (time.monotonic() + remaining, rows)
        return rows


def invalidate(actor):
    lock, entries = _BUCKETS[hash(actor) % len(_BUCKETS)]
    with lock:
        for key in [key for key in entries if key[0] == actor]:
            del entries[key]
