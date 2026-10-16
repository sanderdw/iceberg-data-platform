"""Source secrets: encrypted at rest with AES-GCM, write-only through the API, scrubbed from logs.

The key (ING_SECRET_KEY, 32 bytes as hex) lives only in the extension's .env. A secret is decrypted
only to hand it to the launcher for one run; no response ever contains it.
"""

import base64
import json
import os
import re
from urllib.parse import quote, quote_plus

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import IngestionError

MASK = "••••••"
# Values shorter than this are not scrubbed from logs: they would mask ordinary words.
MIN_SCRUB = 4
PATTERNS = [re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]{8,}"),
            re.compile(r"(?i)(basic\s+)[a-z0-9+/=]{8,}"),
            re.compile(r"(?i)((?:token|password|secret|api[_-]?key)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]{4,}"),
            # requests names the failed URL with its query, where an API key may travel under any name.
            re.compile(r"(?i)(\bfor url: [^\s?]+\?)\S+")]


def forms(value):
    """A secret as it may appear in a log: as is, URL-encoded, or escaped in a repr or JSON string."""
    return {value, quote(value, safe=""), quote(value), quote_plus(value), repr(value)[1:-1], json.dumps(value)[1:-1]}


class Secrets:
    def __init__(self, key):
        self.key = bytes.fromhex(key) if key else None
        if self.key is not None and len(self.key) != 32:
            raise ValueError("ING_SECRET_KEY must be 32 bytes as hex. Run scripts/setup.py.")

    def cipher(self):
        if self.key is None:
            raise IngestionError(503, "Source secrets are not configured: ING_SECRET_KEY is missing. "
                                      "Run scripts/setup.py.", "secrets_unavailable")
        return AESGCM(self.key)

    def seal(self, name, value):
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self.cipher().encrypt(nonce, value.encode(), name.encode())).decode()

    def open(self, name, sealed):
        data = base64.b64decode(sealed)
        try:
            return self.cipher().decrypt(data[:12], data[12:], name.encode()).decode()
        except InvalidTag:
            raise IngestionError(500, f"Secret {name} cannot be read with this ING_SECRET_KEY. Set it again.",
                                 "secret_unreadable") from None

    def reveal(self, stored):
        return {name: self.open(name, sealed) for name, sealed in (stored or {}).items()}

    def scrub(self, text, values=()):
        for value in sorted({f for v in values if v and len(v) >= MIN_SCRUB for f in forms(v)}, key=len, reverse=True):
            text = text.replace(value, MASK)
        for pattern in PATTERNS:
            text = pattern.sub(lambda m: m.group(1) + MASK, text)
        return text

    def scrub_lines(self, lines, values=()):
        return [self.scrub(line, values) for line in lines]
