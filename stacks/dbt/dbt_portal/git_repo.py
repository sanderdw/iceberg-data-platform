"""Git-backed dbt projects: one bare repository per project, written with plumbing only.

Every write is a single commit on a branch (no working trees, no checkout), so agents batch
their edits and every run can name the exact revision it built. `git` always runs with an
argument list and a scrubbed environment, never through a shell.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .errors import DbtError

BRANCH = re.compile(r"^(?!.*\.\.)(?!.*//)(?!/)(?!.*/$)(?!.*\.lock$)[A-Za-z0-9._/-]{1,100}$")
REVISION = re.compile(r"^[0-9a-f]{7,40}$")
# Generated for every run from the platform: the project must not carry its own.
RESERVED = {"profiles.yml", "catalogs.yml"}
MAX_FILE = 1024 * 1024
MAX_FILES = 2000


def valid_path(path, *, write=True):
    parts = path.split("/")
    if (not path or len(path) > 300 or path.startswith("/") or "\\" in path or "\0" in path
            or any(p in ("", ".", "..") or p.startswith(".git") for p in parts)):
        raise DbtError(422, f"Invalid file path: {path[:80]!r}.", "invalid_path")
    if write and path in RESERVED:
        raise DbtError(422, f"{path} is generated for every run from your team's databases; do not commit it.",
                       "reserved_path")
    return path


def valid_branch(name):
    if not BRANCH.match(name or "") or name.startswith("-"):
        raise DbtError(422, "Branch names use letters, digits, '.', '_', '-' and '/'.", "invalid_branch")
    return name


class GitRepos:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, project):
        return self.root / f"{project}.git"

    def git(self, project, *args, input=None, env=None, check=True):
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root),
            "GIT_DIR": str(self.path(project)), "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C", **(env or {}),
        }
        result = subprocess.run(["git", *args], input=input, capture_output=True, env=environment, timeout=60,
                                check=False)
        if check and result.returncode != 0:
            raise DbtError(409 if b"not a valid" in result.stderr or b"bad revision" in result.stderr else 502,
                           "The project repository refused the operation.", "git_error",
                           result.stderr.decode(errors="replace")[-400:])
        return result

    def create(self, project, files, author):
        subprocess.run(["git", "init", "--bare", "--quiet", "--initial-branch=main", str(self.path(project))],
                       check=True, capture_output=True,
                       env={"PATH": os.environ.get("PATH", ""), "HOME": str(self.root)})
        return self.commit(project, "main", files, "Create project from the starter template", author, create=True)

    def delete(self, project):
        import shutil

        shutil.rmtree(self.path(project), ignore_errors=True)

    def resolve(self, project, ref):
        ref = ref or "main"
        if not REVISION.match(ref):
            valid_branch(ref)
            ref = f"refs/heads/{ref}"
        result = self.git(project, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        if result.returncode != 0:
            raise DbtError(404, "No such branch or revision.", "unknown_ref")
        return result.stdout.decode().strip()

    def branches(self, project):
        out = self.git(project, "for-each-ref", "--format=%(refname:short) %(objectname) %(committerdate:unix) "
                                                  "%(contents:subject)", "refs/heads").stdout.decode()
        branches = []
        for line in out.splitlines():
            name, revision, at, *subject = line.split(" ", 3)
            branches.append({"name": name, "revision": revision, "committedAt": int(at),
                             "subject": subject[0] if subject else ""})
        return sorted(branches, key=lambda b: (b["name"] != "main", b["name"]))

    def files(self, project, ref):
        revision = self.resolve(project, ref)
        out = self.git(project, "ls-tree", "-r", "-z", "--long", revision).stdout.decode()
        files = []
        for entry in filter(None, out.split("\0")):
            meta, path = entry.split("\t", 1)
            _, kind, _, size = meta.split()
            if kind == "blob":
                files.append({"path": path, "size": int(size)})
        return revision, files

    def read(self, project, ref, path):
        revision = self.resolve(project, ref)
        result = self.git(project, "cat-file", "blob", f"{revision}:{valid_path(path, write=False)}", check=False)
        if result.returncode != 0:
            raise DbtError(404, "No such file at this revision.", "unknown_file")
        return revision, result.stdout

    def commit(self, project, branch, changes, message, author, *, expected=None, create=False):
        """Apply `changes` ({path: text or None to delete}) as one commit on `branch`."""
        valid_branch(branch)
        if not changes:
            raise DbtError(422, "Nothing to commit.", "empty_commit")
        if len(changes) > 200:
            raise DbtError(413, "Commit at most 200 files at once.", "too_large")
        message = (message or "").strip()[:2000]
        if not message:
            raise DbtError(422, "A commit message is required.", "invalid_request")
        head = None
        if not create:
            head = self.resolve(project, branch)
            if expected and head != expected and not head.startswith(expected):
                raise DbtError(409, "The branch moved since you read it. Read the files again and retry.",
                               "stale_branch", {"head": head})
        identity = {"GIT_AUTHOR_NAME": author["name"], "GIT_AUTHOR_EMAIL": author["email"],
                    "GIT_COMMITTER_NAME": author["name"], "GIT_COMMITTER_EMAIL": author["email"]}
        with tempfile.TemporaryDirectory() as tmp:
            index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if head:
                self.git(project, "read-tree", head, env=index)
            for path, content in sorted(changes.items()):
                valid_path(path)
                if content is None:
                    # Mode 0 removes the entry; unlike --force-remove it needs no work tree.
                    self.git(project, "update-index", "--index-info", env=index,
                             input=f"0 {'0' * 40}\t{path}\n".encode())
                    continue
                data = content.encode() if isinstance(content, str) else content
                if len(data) > MAX_FILE:
                    raise DbtError(413, f"{path} is larger than 1 MB.", "too_large")
                blob = self.git(project, "hash-object", "-w", "--stdin", input=data).stdout.decode().strip()
                self.git(project, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=index)
            tree = self.git(project, "write-tree", env=index).stdout.decode().strip()
        if head and tree == self.git(project, "rev-parse", f"{head}^{{tree}}").stdout.decode().strip():
            raise DbtError(422, "The changes are identical to the current files.", "empty_commit")
        count = self.git(project, "ls-tree", "-r", "--name-only", tree).stdout.count(b"\n")
        if count > MAX_FILES:
            raise DbtError(413, f"A project holds at most {MAX_FILES} files.", "too_large")
        parents = ["-p", head] if head else []
        revision = self.git(project, "commit-tree", tree, *parents, "-F", "-", input=message.encode(),
                            env=identity).stdout.decode().strip()
        # Compare-and-swap: a concurrent commit on the same branch makes this fail instead of losing work.
        old = head or "0" * 40
        result = self.git(project, "update-ref", f"refs/heads/{branch}", revision, old, check=False)
        if result.returncode != 0:
            raise DbtError(409, "The branch moved while committing. Read the files again and retry.", "stale_branch")
        return revision

    def create_branch(self, project, name, source="main"):
        valid_branch(name)
        revision = self.resolve(project, source)
        result = self.git(project, "update-ref", f"refs/heads/{name}", revision, "0" * 40, check=False)
        if result.returncode != 0:
            raise DbtError(409, "This branch already exists.", "branch_exists")
        return revision

    def delete_branch(self, project, name):
        if valid_branch(name) == "main":
            raise DbtError(422, "The main branch cannot be deleted.", "protected_branch")
        revision = self.resolve(project, name)
        self.git(project, "update-ref", "-d", f"refs/heads/{name}", revision)

    def log(self, project, ref, limit=30):
        revision = self.resolve(project, ref)
        out = self.git(project, "log", f"--max-count={min(limit, 200)}", "--format=%H%x1f%an%x1f%ct%x1f%s%x1e",
                       revision).stdout.decode()
        commits = []
        for record in filter(None, (r.strip() for r in out.split("\x1e"))):
            sha, author, at, subject = record.split("\x1f")
            commits.append({"revision": sha, "author": author, "committedAt": int(at), "subject": subject})
        return commits

    def diff(self, project, base, head):
        base, head = self.resolve(project, base), self.resolve(project, head)
        stat = self.git(project, "diff", "--numstat", base, head).stdout.decode()
        rows = (line.split("\t", 2) for line in stat.splitlines())
        files = [{"path": p, "added": a, "removed": r} for a, r, p in rows]
        patch = self.git(project, "diff", "--no-color", base, head).stdout.decode(errors="replace")
        return {"base": base, "head": head, "files": files, "patch": patch[:200_000],
                "truncated": len(patch) > 200_000}

    def merge(self, project, branch, message, author):
        """Merge `branch` into main: a fast-forward when possible, otherwise a merge commit."""
        source, target = self.resolve(project, branch), self.resolve(project, "main")
        if self.git(project, "merge-base", "--is-ancestor", source, target, check=False).returncode == 0:
            return {"revision": target, "merged": False}
        if self.git(project, "merge-base", "--is-ancestor", target, source, check=False).returncode == 0:
            revision = source
        else:
            result = self.git(project, "merge-tree", "--write-tree", "--name-only", "--no-messages", target, source,
                              check=False)
            lines = result.stdout.decode().splitlines()
            if result.returncode == 1:
                raise DbtError(409, "The branch conflicts with main. Update the branch from main first.",
                               "merge_conflict", {"files": lines[1:]})
            if result.returncode != 0:
                raise DbtError(502, "The merge failed.", "git_error")
            identity = {"GIT_AUTHOR_NAME": author["name"], "GIT_AUTHOR_EMAIL": author["email"],
                        "GIT_COMMITTER_NAME": author["name"], "GIT_COMMITTER_EMAIL": author["email"]}
            revision = self.git(project, "commit-tree", lines[0], "-p", target, "-p", source, "-F", "-",
                                input=(message or f"Merge {branch} into main").encode(), env=identity,
                                ).stdout.decode().strip()
        if self.git(project, "update-ref", "refs/heads/main", revision, target, check=False).returncode != 0:
            raise DbtError(409, "main moved while merging. Retry.", "stale_branch")
        return {"revision": revision, "merged": True}

    def archive(self, project, revision):
        return self.git(project, "archive", "--format=tar", revision).stdout

    def is_on_main(self, project, revision):
        main = self.resolve(project, "main")
        return self.git(project, "merge-base", "--is-ancestor", revision, main, check=False).returncode == 0
