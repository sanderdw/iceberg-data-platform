"""The public artifact must preserve source and reject accidental private additions."""

import hashlib
import secrets
import shutil
import tarfile

import pytest

from scripts.release import ROOT, build, check, release_files


@pytest.fixture
def release_tree(tmp_path):
    root = tmp_path / "source"
    for path in release_files():
        target = root / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return root


def test_source_archive_is_reproducible_and_excludes_local_state(release_tree):
    (release_tree / ".env").write_text("LOCAL_PASSWORD=" + secrets.token_hex(24))
    (release_tree / ".venv").mkdir()
    (release_tree / ".venv" / "private.txt").write_text("private data")
    first = build(release_tree)
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    second = build(release_tree)
    assert hashlib.sha256(second.read_bytes()).hexdigest() == digest
    with tarfile.open(first) as archive:
        members = archive.getnames()
        assert any(name.endswith("/.env.example") for name in members)
        assert any(name.endswith("/LICENSE") for name in members)
        assert any(name.endswith("/01_pyiceberg_neighborhood.py") for name in members)
        screenshot = next(name for name in members if name.endswith("/docs/portal.png"))
        assert archive.extractfile(screenshot).read() == (release_tree / "docs" / "portal.png").read_bytes()
        assert not any(name.endswith("/.env") or "/.venv/" in name or "/dist/" in name for name in members)


def test_known_local_secret_in_public_source_is_rejected(release_tree):
    secret = secrets.token_hex(24)
    (release_tree / ".env").write_text("LOCAL_PASSWORD=" + secret)
    (release_tree / "server" / "accidental.py").write_text("credential = " + repr(secret))
    with pytest.raises(ValueError, match="Local credential") as exc:
        check(release_tree)
    assert secret not in str(exc.value)


def test_symlink_and_unexpected_private_file_are_rejected(release_tree, tmp_path):
    private = release_tree / "user_portal" / "key.json"
    private.write_text("{}")
    with pytest.raises(ValueError, match="Unexpected file"):
        check(release_tree)
    private.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (release_tree / "docs" / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        check(release_tree)
