"""Mask the catalog token and the source's credentials in everything a run writes."""

import re

PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:token|secret|password|api[_-]?key|access[_-]?key[_-]?id)=)[^&\s'\"]+"),
    re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+(@)"),
]
MASK = "***"


class Scrubber:
    def __init__(self, values=()):
        # Longest first, so a secret that contains another is masked whole.
        self.values = sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True)

    def __call__(self, text):
        for value in self.values:
            text = text.replace(value, MASK)
        for pattern in PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + MASK + (m.group(2) if m.lastindex == 2 else ""), text)
        return text
