"""Query results kept briefly in memory, so charts and tables load rows by id instead of from the LLM.

A result belongs to the person who asked for it and names the model it came from; reading it
again re-checks that the person can still read that model.
"""

import secrets
import time
from collections import OrderedDict


class ResultStore:
    def __init__(self, *, ttl=1800, capacity=500, per_owner=50):
        self.ttl = ttl
        self.capacity = capacity
        self.per_owner = per_owner
        self._items = OrderedDict()

    def _expire(self):
        now = time.monotonic()
        for key in [k for k, v in self._items.items() if v["until"] <= now]:
            del self._items[key]

    def put(self, owner, result):
        self._expire()
        mine = [k for k, v in self._items.items() if v["owner"] == owner]
        for key in mine[: max(0, len(mine) - self.per_owner + 1)]:
            del self._items[key]
        while len(self._items) >= self.capacity:
            self._items.popitem(last=False)
        id = "res-" + secrets.token_hex(12)
        self._items[id] = {"owner": owner, "until": time.monotonic() + self.ttl, "result": {**result, "id": id}}
        return id

    def get(self, owner, id):
        self._expire()
        item = self._items.get(id)
        if item is None or item["owner"] != owner:
            return None
        return item["result"]
