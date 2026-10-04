"""Query results kept briefly in memory, so charts and tables load rows by id instead of from the LLM.

A result belongs to the person who asked for it and names the model it came from; reading it
again re-checks that the person can still read that model and the tables in its guard, which is
never returned to clients.
"""

import json
import secrets
import time
from collections import OrderedDict


class ResultStore:
    # Budgets in bytes of JSON. One result is at most 16 MB of worker output, and its Python objects take
    # several times its JSON size, so a count alone could hold more than the gateway's memory.
    def __init__(self, *, ttl=1800, capacity=500, per_owner=50, max_bytes=256_000_000, per_owner_bytes=64_000_000):
        self.ttl = ttl
        self.capacity = capacity
        self.per_owner = per_owner
        self.max_bytes = max_bytes
        self.per_owner_bytes = per_owner_bytes
        self._items = OrderedDict()

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, v in self._items.items() if v["until"] <= now]:
            del self._items[key]

    def put(self, owner, result, guard=None):
        self._expire()
        size = len(json.dumps(result, separators=(",", ":"), default=str))
        # The owner's oldest results make room first, then everyone's oldest.
        mine = [k for k, v in self._items.items() if v["owner"] == owner]
        while mine and (len(mine) >= self.per_owner
                        or sum(self._items[k]["bytes"] for k in mine) + size > self.per_owner_bytes):
            del self._items[mine.pop(0)]
        while self._items and (len(self._items) >= self.capacity
                               or sum(v["bytes"] for v in self._items.values()) + size > self.max_bytes):
            self._items.popitem(last=False)
        id = "res-" + secrets.token_hex(12)
        self._items[id] = {"owner": owner, "until": time.monotonic() + self.ttl, "result": {**result, "id": id},
                           "guard": guard or {"shared": False}, "bytes": size}
        return id

    def get(self, owner, id):
        self._expire()
        item = self._items.get(id)
        if item is None or item["owner"] != owner:
            return None
        return item["result"]

    def guard(self, owner, id):
        item = self._items.get(id)
        return item["guard"] if item is not None and item["owner"] == owner else {"shared": False}
