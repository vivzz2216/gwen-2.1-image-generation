"""Ephemeral browser leases for local model caches; no prompts or Redis needed."""
import threading
import time


class ModelSessions:
    def __init__(self, grace=30, timeout=90, hidden_timeout=600, clock=time.monotonic):
        self.grace = grace
        self.timeout = timeout
        self.hidden_timeout = hidden_timeout
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.idle_since = None
        self.dirty = False

    def _expire(self, now):
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        if not self.sessions and self.dirty and self.idle_since is None:
            self.idle_since = now

    def heartbeat(self, session_id, hidden=False):
        with self.lock:
            now = self.clock()
            self._expire(now)
            self.sessions[session_id] = now + (self.hidden_timeout if hidden else self.timeout)
            self.idle_since = None
            self.dirty = True
            return self.snapshot()

    def close(self, session_id):
        with self.lock:
            self.sessions.pop(session_id, None)
            self._expire(self.clock())
            return self.snapshot()

    def activity(self):
        """Also release caches eventually when a script uses the API without a tab."""
        with self.lock:
            self.dirty = True
            self._expire(self.clock())

    def snapshot(self):
        with self.lock:
            now = self.clock()
            self._expire(now)
            return {"active_tabs": len(self.sessions), "cache_retained": self.dirty,
                    "release_in_sec": max(0, round(self.grace - (now - self.idle_since), 1))
                    if self.idle_since is not None and self.dirty else None,
                    "close_grace_sec": self.grace}

    def has_tabs(self):
        return self.snapshot()["active_tabs"] > 0

    def release_if_due(self, release):
        # Synchronize the final check and release with new heartbeats. A callback
        # must never block on the generation lock; it returns False while busy.
        with self.lock:
            now = self.clock()
            self._expire(now)
            if self.sessions or not self.dirty or self.idle_since is None or now - self.idle_since < self.grace:
                return False
            if not release():
                return False
            self.dirty = False
            self.idle_since = None
            return True
