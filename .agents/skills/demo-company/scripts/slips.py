# /// script
# requires-python = ">=3.10"
# dependencies = ["segno>=1.6"]
# ///
"""Printable sign-in slips, one per participant, from a demo-company credentials file.

    uv run .agents/skills/demo-company/scripts/slips.py demo-company-energy-credentials.md

Writes demo-company-<domain>-slips.html next to the credentials file, with owner-only
permissions: it holds the same live one-time passwords. The portal address comes from
USER_ORIGIN in the .env next to it, so the slips follow a quick share. --portal overrides it.
"""

import argparse
import html
import os
import re
import sys
from pathlib import Path

ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$")


def parse(text):
    """Company, portal address and participants of a credentials file in the skill's format."""
    company = re.search(r"^# (.+?) demo accounts", text, re.MULTILINE)
    portal = re.search(r"^User portal:\s+(\S+)", text, re.MULTILINE)
    people, team = [], None
    for line in text.splitlines():
        if heading := re.match(r"^## ([^:]+)", line):
            team = heading[1].strip()
        elif (row := ROW.match(line)) and team:
            number, username, name, role, password = row.groups()
            people.append({"number": int(number), "username": username, "name": name, "role": role,
                           "team": team, "password": password})
    return (company[1] if company else "Demo company"), (portal[1] if portal else None), people


def env_value(path, key):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    values = [line.split("=", 1)[1].strip() for line in lines if line.startswith(f"{key}=")]
    return values[-1] if values else None


def qr(url):
    import segno

    return segno.make(url, error="m", micro=False).svg_inline(scale=1, border=0, omitsize=True)


def slip(person, company, portal, code):
    e = html.escape
    # A real one-time password has no spaces; anything else is a note such as "existing, password unchanged".
    password = person["password"]
    if password and " " not in password:
        secret = (f'<dt>PASSWORD</dt><dd class="secret">{e(password)}</dd>'
                  '<dd class="flag">ONE-TIME · YOU CHOOSE A NEW ONE</dd>')
        first = "Sign in and choose your own password."
    else:
        secret = f'<dt>PASSWORD</dt><dd class="note">{e(password or "Ask your trainer")}</dd>'
        first = "Sign in with your existing password."
    return f"""<article class="slip">
  <div class="head">
    <p class="label">{e(company.upper())} / {person["number"]:02d}</p>
    <h2>{e(person["name"])}</h2>
    <p class="team">{e(person["team"])} · {e(person["role"])}</p>
  </div>
  <div class="qr" aria-hidden="true">{code}</div>
  <dl><dt>USERNAME</dt><dd class="secret">{e(person["username"])}</dd>{secret}</dl>
  <div class="go">
    <p class="url">{e(portal)}</p>
    <ol><li>{first}</li><li>Follow Getting started in the portal.</li></ol>
  </div>
</article>"""


def render(company, portal, people):
    code = qr(portal)
    slips = "\n".join(slip(p, company, portal, code) for p in people)
    title = html.escape(f"{company} sign-in slips")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Doto:wght@700&family=Space+Grotesk:wght@400;500&family=Space+Mono&display=swap">
<style>
@page {{ size: A4; margin: 10mm; }}
:root {{
  --paper: #fff; --page: #f5f5f5; --ink: #000; --text: #1a1a1a; --secondary: #666; --cut: #ccc; --accent: #d71921;
  --body: 'Space Grotesk', system-ui, sans-serif; --mono: 'Space Mono', ui-monospace, monospace;
  font: 10pt/1.45 var(--body); color: var(--text); background: var(--page);
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 48px 16px; }}
header {{ max-width: 190mm; margin: 0 auto 32px; }}
header h1 {{ font: 700 48px/1 Doto, var(--mono); color: var(--ink); margin: 0 0 16px; }}
header p {{ margin: 0 0 8px; max-width: 560px; color: var(--secondary); }}
header .warn {{ color: var(--text); padding-left: 12px; border-left: 2px solid var(--accent); }}
.label, dt, .flag, .url {{ font: 7.5pt/1.4 var(--mono); letter-spacing: .06em; }}
.sheet {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 190mm; margin: 0 auto; background: var(--paper); }}
.slip {{ height: 68mm; padding: 5mm 6mm; overflow: hidden; outline: 1px dashed var(--cut); outline-offset: -.5px; break-inside: avoid;
  display: grid; grid-template-columns: minmax(0, 1fr) 22mm; grid-template-rows: auto auto 1fr; column-gap: 4mm; }}
.label {{ margin: 0; color: var(--secondary); }}
h2 {{ margin: 2mm 0 0; font: 500 13pt/1.2 var(--body); color: var(--ink); }}
.team {{ margin: 1mm 0 0; color: var(--secondary); }}
.qr svg {{ display: block; width: 22mm; height: 22mm; }}
dl {{ grid-column: 1 / -1; display: grid; grid-template-columns: 22mm minmax(0, 1fr); gap: .5mm 0; margin: 3mm 0 0; align-items: baseline; }}
dt {{ color: var(--secondary); }}
dd {{ margin: 0; }}
.secret {{ font: 11pt/1.3 var(--mono); color: var(--ink); overflow-wrap: anywhere; }}
.flag {{ grid-column: 2; color: var(--accent); }}
.note {{ color: var(--secondary); }}
.go {{ grid-column: 1 / -1; align-self: end; border-top: 1px solid var(--cut); padding-top: 2mm; }}
.url {{ margin: 0 0 1.5mm; color: var(--ink); overflow-wrap: anywhere; }}
ol {{ margin: 0; padding-left: 4mm; font-size: 8pt; color: var(--secondary); }}
@media print {{
  :root, body {{ padding: 0; background: var(--paper); }}
  header {{ display: none; }}
  .sheet {{ max-width: none; }}
}}
@media (max-width: 640px) {{
  .sheet {{ grid-template-columns: 1fr; }}
  .slip {{ height: auto; min-height: 68mm; }}
}}
</style>
</head>
<body>
<header>
  <h1>Sign-in slips</h1>
  <p>{len(people)} participants of {html.escape(company)}. Print on A4, cut along the dashed lines and hand one slip to each participant.</p>
  <p>Portal: {html.escape(portal)}</p>
  <p class="warn">These slips hold live one-time passwords. Delete this file once they are handed out.</p>
</header>
<main class="sheet">
{slips}
</main>
</body>
</html>
"""


def write_private(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    if os.name != "nt":
        os.chmod(path, 0o600)  # Also when the file already existed with wider permissions.


def main(argv=None):
    parser = argparse.ArgumentParser(description="Printable sign-in slips from a demo-company credentials file.")
    parser.add_argument("credentials", type=Path, help="demo-company-<domain>-credentials.md")
    parser.add_argument("--portal", help="user portal address (default: USER_ORIGIN from the .env next to the file)")
    args = parser.parse_args(argv)
    company, listed, people = parse(args.credentials.read_text(encoding="utf-8"))
    portal = (args.portal or env_value(args.credentials.parent / ".env", "USER_ORIGIN") or listed or "").rstrip("/")
    if not portal:
        sys.exit("No user portal address. Pass --portal.")
    if not people:
        sys.exit(f"No participants found in {args.credentials}.")
    name = args.credentials.name.removesuffix("-credentials.md").removesuffix(".md") + "-slips.html"
    output = args.credentials.with_name(name)
    write_private(output, render(company, portal, people))
    print(f"{len(people)} slips for {portal} in {output}")


if __name__ == "__main__":
    main()
