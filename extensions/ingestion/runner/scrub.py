"""Mask the catalog token and the source's credentials in everything a run writes."""

import json
import re
from urllib.parse import quote, quote_plus

PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:token|secret|password|api[_-]?key|access[_-]?key[_-]?id)=)[^&\s'\"]+"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+(@)"),
    re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}"),
    # requests names the failed URL with its query, where an API key may travel under any name.
    re.compile(r"(?i)(\bfor url: [^\s?]+\?)\S+"),
]
MASK = "***"


def forms(value):
    """A secret as it may appear in a log: as is, URL-encoded, or escaped in a repr or JSON string."""
    return {value, quote(value, safe=""), quote(value), quote_plus(value), repr(value)[1:-1], json.dumps(value)[1:-1]}


class Scrubber:
    def __init__(self, values=()):
        # Longest first, so a secret that contains another is masked whole.
        self.values = sorted({f for v in values if v and len(v) >= 4 for f in forms(v)}, key=len, reverse=True)

    def __call__(self, text):
        for value in self.values:
            text = text.replace(value, MASK)
        for pattern in PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + MASK + (m.group(2) if m.lastindex == 2 else ""), text)
        return text
