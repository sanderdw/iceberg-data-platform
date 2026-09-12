"""Generate local credentials without overwriting existing configuration."""

import re
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
template = (root / ".env.example").read_text()
content = re.sub(r"replace-with-generated-(password|secret)", lambda _: secrets.token_hex(24), template)
try:
    with (root / ".env").open("x") as file:
        (root / ".env").chmod(0o600)
        file.write(content)
    print(".env created. The local portal password is stored under PORTAL_PASSWORD.")
except FileExistsError:
    print(".env already exists; existing configuration preserved.")
