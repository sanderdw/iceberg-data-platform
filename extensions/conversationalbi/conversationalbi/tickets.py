"""How a browser session reaches the agent through the CopilotKit runtime without the runtime holding it.

The gateway checks the session cookie, mints a short-lived opaque run ticket and adds it as the
`X-CBI-Run-Ticket` header when it proxies a request to the runtime. The runtime forwards only that
header to the agent listener, which turns the ticket back into the session. A ticket is useless
outside this process, and the runtime never sees the cookie or a Keycloak token.

The runtime keeps conversation threads by id. The gateway binds each thread id to the person who
used it first, so nobody can connect to or stop someone else's thread.
"""

import secrets
import time
from collections import OrderedDict


class RunTickets:
    def __init__(self, ttl=300):
        self.ttl = ttl
        self._tickets = {}

    def issue(self, session_id, subject):
        now = time.monotonic()
        self._tickets = {k: v for k, v in self._tickets.items() if v[0] > now}
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (now + self.ttl, session_id, subject)
        return ticket

    def resolve(self, ticket):
        found = self._tickets.get(ticket or "")
        if not found or found[0] <= time.monotonic():
            return None
        return found[1], found[2]


class ThreadOwners:
    def __init__(self, capacity=20000):
        self.capacity = capacity
        self._owners = OrderedDict()

    def claim(self, thread_id, subject):
        """True when the thread is new or already this person's."""
        owner = self._owners.get(thread_id)
        if owner is None:
            self._owners[thread_id] = subject
            while len(self._owners) > self.capacity:
                self._owners.popitem(last=False)
            return True
        self._owners.move_to_end(thread_id)
        return owner == subject
