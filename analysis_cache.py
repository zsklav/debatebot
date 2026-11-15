"""Bounded, process-local analysis reuse; failures are never cached."""

from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from threading import RLock
import time


class AnalysisCache:
    def __init__(self, *, max_entries=128, ttl=600, clock=time.monotonic):
        self.max_entries, self.ttl, self.clock = max_entries, ttl, clock
        self.entries, self.pending = OrderedDict(), {}
        self.lock = RLock()

    def run(self, key, compute):
        """Reuse successful results and share simultaneous requests for one key.

        The caller supplies an owner-scoped, content/model-specific key.
        Provider work runs outside the lock so unrelated users remain independent.
        """
        with self.lock:
            now = self.clock()
            for stale in [k for k, (expiry, _) in self.entries.items() if expiry <= now]:
                del self.entries[stale]
            if key in self.entries:
                self.entries.move_to_end(key)
                return deepcopy(self.entries[key][1])
            future = self.pending.get(key)
            producer = future is None
            if producer:
                future = self.pending[key] = Future()
        if not producer:
            return deepcopy(future.result())
        try:
            result = compute()
            with self.lock:
                self.entries[key] = (self.clock() + self.ttl, deepcopy(result))
                while len(self.entries) > self.max_entries:
                    self.entries.popitem(last=False)
                future.set_result(deepcopy(result))
            return result
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self.lock:
                self.pending.pop(key, None)
