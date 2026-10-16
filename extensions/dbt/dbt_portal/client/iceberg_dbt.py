#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.28"]
# ///
"""dbt commands from your terminal, run on the Iceberg Data Platform.

    uv run iceberg_dbt.py login                     # sign in once in your browser
    uv run iceberg_dbt.py projects                  # the dbt projects of your teams
    uv run iceberg_dbt.py clone PROJECT [FOLDER]    # the project's files from main
    uv run iceberg_dbt.py build --select stg_orders+
    uv run iceberg_dbt.py test | run | seed | compile | parse | docs
    uv run iceberg_dbt.py show --select stg_orders --limit 10
    uv run iceberg_dbt.py compile --inline "select * from {{ ref('stg_orders') }}"
    uv run iceberg_dbt.py pull | status
    uv run iceberg_dbt.py push --branch NAME [-m MESSAGE]
    uv run iceberg_dbt.py build --ref NAME          # build a branch instead of main
    uv run iceberg_dbt.py logout

dbt runs on the platform, never on this computer: as your team's service account, in development
unless you pass --target acceptance or --target production. Commands build `main` by default.
Your local edits reach the platform only through a branch (`push`), which a team administrator
merges into main. The dbt MCP server (`uvx dbt-mcp`) can use this script as its DBT_PATH.

Your Keycloak account and your team role decide what you may do. The refresh token is stored in
~/.config/iceberg-platform with owner-only permissions.
"""

import argparse
import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path, PurePosixPath, PureWindowsPath

import httpx

# Defaults match a local installation; the dbt viewer fills in these values when you download this file.
ORIGIN = "http://localhost:3004"
ISSUER = "http://localhost:8080/realms/iceberg"
CLIENT_ID = "ext-dbt-mcp"

STATE = ".iceberg-dbt.json"
# Never sent to the platform: generated per run, local output, or tool state.
IGNORED_DIRS = {"target", "logs", "dbt_packages", "dbt_internal_packages", ".git", ".venv", "__pycache__"}
IGNORED_FILES = {STATE, "profiles.yml", "catalogs.yml", ".user.yml"}
RUN_COMMANDS = ("build", "run", "test", "seed", "compile", "parse", "docs")
ENVIRONMENTS = ("development", "acceptance", "production")
LOGIN_AGAIN = "Sign in again with: uv run iceberg_dbt.py login"


class Failure(RuntimeError):
    pass


def self_command():
    """How to call this script again from the current folder, for hints."""
    script = Path(sys.argv[0]).resolve()
    if script.suffix != ".py" or not script.exists():
        return "uv run iceberg_dbt.py"
    relative = os.path.relpath(script)
    return f"uv run {relative if len(relative) < len(str(script)) else script}"


def cell(value, width=40):
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[:width - 1] + "…"


# Sign-in: authorization code with PKCE on a loopback address, like the MCP clients.

def credentials_path(origin=ORIGIN):
    name = "dbt-" + hashlib.sha256(origin.encode()).hexdigest()[:16] + ".json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "iceberg-platform" / name


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(data, file)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def expiry(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
    except (IndexError, KeyError, ValueError):
        return 0


class Session:
    def __init__(self, issuer=ISSUER, client_id=CLIENT_ID, path=None, http=None):
        self.issuer, self.client_id = issuer.rstrip("/"), client_id
        self.path = path or credentials_path()
        self.http = http or httpx.Client(timeout=30)
        self._token = None

    def endpoint(self, name):
        return f"{self.issuer}/protocol/openid-connect/{name}"

    def store(self, issued):
        self._token = issued["access_token"]
        if "refresh_token" in issued:
            write_private(self.path, {"issuer": self.issuer, "refresh_token": issued["refresh_token"]})

    def login(self, open_browser=webbrowser.open):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state, received = secrets.token_urlsafe(16), {}

        class Callback(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                received.update({key: values[0] for key, values in query.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Signed in. You can close this tab and return to the terminal.")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = self.endpoint("auth") + "?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": redirect,
            "scope": "openid offline_access", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        print(f"Opening your browser to sign in. If it does not open, visit:\n{url}", flush=True)
        with contextlib.suppress(Exception):
            open_browser(url)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        thread.join(300)
        server.server_close()
        if received.get("state") != state or "code" not in received:
            raise Failure("Sign-in did not complete. " + LOGIN_AGAIN)
        response = self.http.post(self.endpoint("token"), data={
            "grant_type": "authorization_code", "client_id": self.client_id, "code": received["code"],
            "redirect_uri": redirect, "code_verifier": verifier})
        response.raise_for_status()
        self.store(response.json())
        print("Signed in.")

    def token(self):
        if self._token and expiry(self._token) > time.time() + 60:
            return self._token
        try:
            stored = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise Failure("You are not signed in. " + LOGIN_AGAIN) from None
        response = self.http.post(self.endpoint("token"), data={
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": stored["refresh_token"]})
        if response.status_code in (400, 401):
            raise Failure("Your sign-in has expired or was revoked. " + LOGIN_AGAIN)
        response.raise_for_status()
        self.store(response.json())
        return self._token

    def logout(self):
        try:
            stored = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        self.http.post(self.endpoint("revoke"), data={
            "client_id": self.client_id, "token": stored["refresh_token"], "token_type_hint": "refresh_token",
        }).raise_for_status()
        self.path.unlink(missing_ok=True)


# The dbt extension's REST API.

class Api:
    def __init__(self, session, origin=ORIGIN, http=None):
        self.session, self.base = session, origin.rstrip("/") + "/api/v1"
        self.http = http or httpx.Client(timeout=660)

    def call(self, method, path, **kwargs):
        try:
            response = self.http.request(method, self.base + path,
                                         headers={"Authorization": f"Bearer {self.session.token()}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise Failure(f"Could not reach the dbt extension at {self.base} ({type(exc).__name__}).") from None
        body = response.json() if "json" in response.headers.get("content-type", "") else {}
        if response.status_code == 401:
            raise Failure((body.get("error") or "Not signed in.") + " " + LOGIN_AGAIN)
        if response.is_error:
            raise Failure(body.get("error") or f"The dbt extension answered {response.status_code}.")
        return body


# The project folder: files from the platform and what changed locally since.

def find_root(start=None):
    folder = Path(start or Path.cwd()).resolve()
    for candidate in (folder, *folder.parents):
        if (candidate / STATE).exists():
            return candidate
    raise Failure("No project here: run this inside a folder made by `uv run iceberg_dbt.py clone`.")


def load_state(root):
    return json.loads((root / STATE).read_text())


def save_state(root, state):
    (root / STATE).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_files(root):
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_dir() or relative.name in IGNORED_FILES or any(
                part in IGNORED_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        files[relative.as_posix()] = path
    return files


def changes(root, state):
    """Paths added, modified and deleted since the last clone or pull."""
    known, here = state["files"], local_files(root)
    added = sorted(p for p in here if p not in known)
    modified = sorted(p for p in here if p in known and digest(here[p].read_bytes()) != known[p])
    deleted = sorted(p for p in known if p not in here)
    return added, modified, deleted


def fetch(api, project, ref):
    listing = api.call("GET", f"/projects/{project}/files", params={"ref": ref})
    contents = {}
    for item in listing["files"]:
        path = item["path"]
        contents[path] = api.call("GET", f"/projects/{project}/files/{urllib.parse.quote(path)}",
                                  params={"ref": listing["revision"]})["content"]
    return listing["revision"], contents


def local_path(root, path):
    """`root / path` for a file path from the server, refused unless it stays inside root on every platform.

    On Windows a drive (`C:/...`, `C:x`) or an anchor replaces the folder when joined, so a path a teammate
    committed could otherwise write anywhere your account can.
    """
    windows = PureWindowsPath(path)
    if (not path or ":" in path or "\\" in path or PurePosixPath(path).is_absolute() or windows.drive or windows.anchor
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise Failure(f"The project holds an unsafe file path, {path[:80]!r}; nothing was written. "
                      "Ask a team administrator to remove it.")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise Failure(f"{path[:80]!r} leads outside {root}; nothing was written.")
    return target


def write_tree(root, contents, keep=()):
    targets = {path: local_path(root, path) for path in contents if path not in keep}  # all checked before any write
    for path, target in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[path].encode())  # bytes: the same digest on every platform, no newline translation


# Commands.

def cmd_projects(api, args):
    for project in api.call("GET", "/projects"):
        print(f"{project['name']:<32} {project['role']:<8} {project['id']}  {project.get('description', '')}")


def cmd_clone(api, args):
    projects = api.call("GET", "/projects")
    match = [p for p in projects if args.project in (p["name"], p["id"])]
    if len(match) != 1:
        raise Failure(f"No single project named {args.project}. `uv run iceberg_dbt.py projects` lists yours.")
    project = match[0]
    root = Path(args.folder or project["name"]).resolve()
    if root.exists() and any(root.iterdir()):
        raise Failure(f"{root} is not empty.")
    root.mkdir(parents=True, exist_ok=True)
    revision, contents = fetch(api, project["id"], args.ref)
    write_tree(root, contents)
    save_state(root, {"origin": ORIGIN, "project": project["id"], "name": project["name"], "ref": args.ref,
                      "revision": revision, "files": {p: digest(t.encode()) for p, t in contents.items()}})
    print(f"Cloned {project['name']} ({args.ref} @ {revision[:7]}) into {root}.")
    script = Path(sys.argv[0]).resolve()
    run = f"uv run {script}" if script.suffix == ".py" and script.exists() else "uv run iceberg_dbt.py"
    print(f"Next: cd {root} && {run} build")


def cmd_pull(api, args):
    root = find_root()
    state = load_state(root)
    added, modified, _ = changes(root, state)
    revision, contents = fetch(api, state["project"], args.ref or state["ref"])
    # Local edits win: files you changed are left alone and named.
    keep = set(modified) | set(added)
    write_tree(root, contents, keep)
    for path in set(state["files"]) - set(contents) - keep:
        local_path(root, path).unlink(missing_ok=True)
    state.update(ref=args.ref or state["ref"], revision=revision,
                 files={p: digest(t.encode()) for p, t in contents.items()})
    save_state(root, state)
    print(f"Up to date with {state['ref']} @ {revision[:7]}.")
    for path in sorted(keep & set(contents)):
        print(f"  kept your version of {path}")


def cmd_status(api, args):
    root = find_root()
    state = load_state(root)
    added, modified, deleted = changes(root, state)
    print(f"{state['name']} · {state['ref']} @ {state['revision'][:7]}")
    if not (added or modified or deleted):
        print("No local changes.")
    for label, paths in (("added", added), ("modified", modified), ("deleted", deleted)):
        for path in paths:
            print(f"  {label:<9} {path}")


def cmd_push(api, args):
    root = find_root()
    state = load_state(root)
    if args.branch == "main":
        raise Failure("main changes only through a merge by a team administrator. Push to a branch.")
    added, modified, deleted = changes(root, state)
    files = {p: (root / p).read_bytes().decode() for p in added + modified} | dict.fromkeys(deleted)
    if not files:
        print("No local changes to push.")
        return
    branches = {b["name"] for b in api.call("GET", f"/projects/{state['project']}/branches")}
    if args.branch not in branches:
        api.call("POST", f"/projects/{state['project']}/branches",
                 json={"name": args.branch, "source": state["revision"]})
    commit = api.call("POST", f"/projects/{state['project']}/commits", json={
        "branch": args.branch, "files": files, "message": args.message or f"Changes from {Path.cwd().name}"})
    state.update(ref=args.branch, revision=commit["revision"],
                 files={**{p: v for p, v in state["files"].items() if p not in deleted},
                        **{p: digest(t.encode()) for p, t in files.items() if t is not None}})
    save_state(root, state)
    print(f"Pushed {len(files)} file(s) to {args.branch} @ {commit['revision'][:7]}.")
    print(f"Build it with: {self_command()} build --ref {args.branch}")
    print("A team administrator merges it into main, in the dbt viewer or with merge_to_main.")


def note_unpushed(root, state, ref):
    added, modified, deleted = changes(root, state)
    if added or modified or deleted:
        count = len(added) + len(modified) + len(deleted)
        print(f"Note: {count} local change(s) are not on the platform; this runs {ref} as it is there. "
              f"Push them with `{self_command()} push --branch <name>` and run with `--ref <name>`.",
              file=sys.stderr)


def follow(api, run):
    printed = 0
    while True:
        lines = api.call("GET", f"/runs/{run['id']}/logs", params={"tail": 5000})["lines"]
        for line in lines[printed:]:
            print(line, flush=True)
        printed = max(printed, len(lines))
        run = api.call("GET", f"/runs/{run['id']}", params={"wait": 5})
        if run["status"] not in ("queued", "running"):
            for line in api.call("GET", f"/runs/{run['id']}/logs", params={"tail": 5000})["lines"][printed:]:
                print(line, flush=True)
            return run


def cmd_run(api, args):
    root = find_root()
    state = load_state(root)
    ref = args.ref or "main"
    note_unpushed(root, state, ref)
    select = " ".join(args.select or [])
    if args.command == "compile" and args.inline:
        result = api.call("POST", f"/projects/{state['project']}/compile",
                          json={"environment": args.target, "ref": ref, "sql": args.inline})
        print(result["output"])
        return 0 if result["status"] == "success" else 1
    run = api.call("POST", f"/projects/{state['project']}/runs", json={
        "command": args.command, "environment": args.target, "ref": ref, "select": select,
        "full_refresh": bool(getattr(args, "full_refresh", False))})
    print(f"Running {args.command} on {ref} in {args.target} ({run['id']}).", file=sys.stderr, flush=True)
    run = follow(api, run)
    print(f"{run['status']}: {ORIGIN}/#/projects/{state['project']}/pipeline?"
          + urllib.parse.urlencode({"environment": args.target, "ref": ref, "run": run["id"]}), file=sys.stderr)
    return 0 if run["status"] == "success" else (run.get("exit_code") or 1)


def cmd_show(api, args):
    root = find_root()
    state = load_state(root)
    ref = args.ref or "main"
    note_unpushed(root, state, ref)
    if args.inline:
        raise Failure("show --inline is not supported on the platform; use --select with one model.")
    if not args.select or len(args.select) != 1:
        raise Failure("Name one model: show --select <model>.")
    result = api.call("POST", f"/projects/{state['project']}/preview", json={
        "model": args.select[0], "environment": args.target, "ref": ref, "limit": args.limit})
    if result["rows"] is None:
        print(result.get("output") or "No rows.")
        return 1
    if args.output == "json":
        print(json.dumps({"show": result["rows"]}, indent=2, default=str))
        return 0
    rows = result["rows"]
    columns = list(rows[0]) if rows else []
    widths = {c: max([len(c), *(len(cell(r.get(c))) for r in rows)]) for c in columns}
    print(" | ".join(c.ljust(widths[c]) for c in columns))
    print("-+-".join("-" * widths[c] for c in columns))
    for row in rows:
        print(" | ".join(cell(row.get(c)).ljust(widths[c]) for c in columns))
    return 0


def parser():
    top = argparse.ArgumentParser(prog="iceberg_dbt.py", description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    top.add_argument("--version", action="version", version="iceberg-dbt CLI · dbt runs on the platform")
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("login")
    sub.add_parser("logout")
    sub.add_parser("projects")
    clone = sub.add_parser("clone")
    clone.add_argument("project")
    clone.add_argument("folder", nargs="?")
    clone.add_argument("--ref", default="main")
    pull = sub.add_parser("pull")
    pull.add_argument("--ref")
    sub.add_parser("status")
    push = sub.add_parser("push")
    push.add_argument("--branch", required=True)
    push.add_argument("-m", "--message")

    def dbt_options(command):
        command.add_argument("-s", "--select", "-m", "--models", nargs="+", dest="select")
        command.add_argument("-t", "--target", choices=ENVIRONMENTS, default="development")
        command.add_argument("--ref", help="branch or revision to run instead of main")
        # Accepted for compatibility with dbt and the dbt MCP server; the platform decides these.
        command.add_argument("-q", "--quiet", action="store_true")
        command.add_argument("--log-format")
        command.add_argument("--no-use-colors", action="store_true")
        command.add_argument("--use-colors", action="store_true")
        command.add_argument("--project-dir")
        command.add_argument("--profiles-dir")
        return command

    for name in RUN_COMMANDS:
        command = dbt_options(sub.add_parser(name))
        if name in ("build", "run", "seed"):
            command.add_argument("--full-refresh", action="store_true")
        if name == "compile":
            command.add_argument("--inline")
        if name == "docs":
            command.add_argument("action", nargs="?", choices=["generate"], default="generate")
    show = dbt_options(sub.add_parser("show"))
    show.add_argument("--inline")
    show.add_argument("--limit", type=int, default=10)
    show.add_argument("--output", choices=["text", "json"], default="text")
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    session = Session()
    if args.command == "login":
        return session.login()
    if args.command == "logout":
        session.logout()
        print("Signed out.")
        return 0
    if getattr(args, "project_dir", None):
        os.chdir(args.project_dir)
    api = Api(session)
    handlers = {"projects": cmd_projects, "clone": cmd_clone, "pull": cmd_pull, "status": cmd_status,
                "push": cmd_push, "show": cmd_show}
    return handlers.get(args.command, cmd_run)(api, args) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
