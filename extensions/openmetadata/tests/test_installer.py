"""Exercise the downloaded installer against a fake platform, without touching the host Docker daemon."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.release import installer_source
from scripts.setup import read

ROOT = Path(__file__).resolve().parent.parent
PLATFORM_SETUP = ROOT.parent.parent / "scripts" / "setup.py"
PORTAL = "ghcr.io/sanderdw/iceberg-data-platform-portal:0.9.1"
TAG = "openmetadata-v0.1.1"
# `docker run` of the platform image: run the script with the mounts mapped to the host and only the
# variables the installer names with -e, as Docker would. The local OpenMetadata's token is faked.
FAKE_RUN = '''import os, subprocess, sys
args = sys.argv[1:]
image = next(i for i, arg in enumerate(args) if arg.startswith("ghcr.io/"))
mounts = {}
for i in range(image):
    if args[i] == "--mount":
        parts = dict(part.split("=", 1) for part in args[i + 1].split(","))
        mounts[parts["dst"]] = parts["src"]
names = [args[i + 1] for i in range(image) if args[i] == "-e"]
def host(arg):
    for dst, src in mounts.items():
        if arg.startswith(dst + "/"):
            return src + arg[len(dst):]
    return arg
command = [host(arg) for arg in args[image + 1:]]
if command[0].endswith("om_token.py"):
    env_file = os.path.join(os.path.dirname(os.path.dirname(command[0])), ".env")
    with open(env_file, "a") as file:
        file.write("OM_JWT_TOKEN=eyJ.local-bot\\n")
    sys.exit(0)
env = {"PATH": os.environ["PATH"], **{name: os.environ[name] for name in names if name in os.environ}}
sys.exit(subprocess.run([sys.executable, *command], env=env, input=b"").returncode)
'''
CURL = '''import os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith('https:'))
with open(os.environ['TEST_DOWNLOAD_LOG'], 'a') as log:
    log.write(url + '\\n')
shutil.copyfile(pathlib.Path(os.environ['TEST_RELEASE']) / url.rsplit('/', 1)[1], args[args.index('-o') + 1])
'''
DOCKER = '''import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ['TEST_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps({"cwd": os.getcwd(), "args": args,
                          "env": sorted(name for name in os.environ if name.startswith("OM_"))}) + '\\n')
if args[0] == 'run':
    sys.exit(subprocess.run([sys.executable, os.environ['TEST_FAKE_RUN'], *args]).returncode)
'''


@pytest.fixture
def installer(tmp_path):
    if sys.platform == "win32":
        pytest.skip("The installer is a POSIX shell script")
    release = tmp_path / "release"
    release.mkdir()
    archive = release / "iceberg-openmetadata-0.1.1-install.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in ("compose.yaml", "compose.openmetadata.yaml", "scripts/setup.py", "scripts/om_token.py"):
            data = (ROOT / name).read_bytes()
            info = tarfile.TarInfo("iceberg-openmetadata-0.1.1-install/" + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    (release / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    platform = tmp_path / "iceberg data platform"
    (platform / "scripts").mkdir(parents=True)
    (platform / "scripts/setup.py").write_bytes(PLATFORM_SETUP.read_bytes())
    (platform / "compose.yaml").write_text(json.dumps({"services": {"portal": {"image": PORTAL}}}))
    (platform / "compose.users.yaml").write_text("{}\n")
    (platform / ".env.example").write_bytes((PLATFORM_SETUP.parent.parent / ".env.example").read_bytes())
    # The platform installer's .env, with another extension already registered.
    subprocess.run([sys.executable, platform / "scripts/setup.py", "--extension", "dbt", "--origin",
                    "http://localhost:3004", "--handshake", platform / "dbt/.env.bridge"],
                   check=True, capture_output=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in {"curl": CURL, "docker": DOCKER}.items():
        (bin_dir / name).write_text(f"#!{sys.executable}\n" + source)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "fake_run.py").write_text(FAKE_RUN)
    inherited = {k: v for k, v in os.environ.items() if not k.startswith("OM_") and k != "ICEBERG_INSTALL_DIR"}
    env = inherited | {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "TEST_RELEASE": str(release),
        "TEST_DOCKER_LOG": str(tmp_path / "docker.jsonl"),
        "TEST_DOWNLOAD_LOG": str(tmp_path / "downloads.txt"),
        "TEST_FAKE_RUN": str(tmp_path / "fake_run.py"),
    }

    def run(*args, source=None, **extra):
        # Piped into sh, as the documented curl command does. A new session has no terminal, so the run
        # is unattended even under an interactive pytest.
        source = source or installer_source(ROOT, TAG).decode()
        return subprocess.run(["sh", "-s", "--", "--dir", str(platform), *args], input=source,
                              env=env | extra, text=True, capture_output=True, check=False, start_new_session=True)

    return run, env, platform


def calls(env):
    path = Path(env["TEST_DOCKER_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def compose_ups(env):
    return [call for call in calls(env) if call["args"][:1] == ["compose"] and "up" in call["args"]]


def test_installs_registers_and_starts_with_settings_from_the_environment(installer):
    run, env, platform = installer
    result = run(OM_URL="https://om.example.com/api", OM_JWT_TOKEN="eyJ.bot", OM_UI_URL="https://om.example.com")
    assert result.returncode == 0, result.stdout + result.stderr
    om_dir = platform / "extensions/openmetadata"
    assert {p.name for p in om_dir.iterdir()} >= {"compose.yaml", "compose.openmetadata.yaml", "scripts", ".env",
                                                   ".env.bridge"}
    downloads = Path(env["TEST_DOWNLOAD_LOG"]).read_text().splitlines()
    base = f"https://github.com/sanderdw/iceberg-data-platform/releases/download/{TAG}/"
    assert downloads == [base + "SHA256SUMS", base + "iceberg-openmetadata-0.1.1-install.tar.gz"]
    platform_env = read(platform / ".env")
    assert platform_env["PLATFORM_EXTENSIONS"] == "dbt=http://localhost:3004,openmetadata=http://localhost:3011"
    assert platform_env["PLATFORM_LISTING_EXTENSIONS"] == "openmetadata"
    assert read(om_dir / ".env.bridge")["BRIDGE_CLIENT_SECRET"] in platform_env["PLATFORM_EXTENSION_SECRETS"]
    settings = read(om_dir / ".env")
    assert (settings["OM_URL"], settings["OM_JWT_TOKEN"], settings["OM_UI_URL"]) == (
        "https://om.example.com/api", "eyJ.bot", "https://om.example.com")
    assert "COMPOSE_FILE" not in settings
    # Setup runs in the platform's own image; the token reaches it by name only.
    runs = [call["args"] for call in calls(env) if call["args"][0] == "run"]
    assert all(PORTAL in args for args in runs) and not any("eyJ.bot" in arg for args in runs for arg in args)
    ups = compose_ups(env)
    assert [Path(call["args"][call["args"].index("-f") + 1]).name for call in ups[:2]] == [
        "compose.yaml", "compose.users.yaml"]
    assert ups[2]["cwd"] == str(om_dir) and ups[2]["args"] == ["compose", "up", "-d", "--wait"]
    assert len(ups) == 3
    assert "The OpenMetadata extension 0.1.1 is running." in result.stdout
    assert "Catalog  https://om.example.com\n" in result.stdout
    assert "Status   http://localhost:3011/api/status\n" in result.stdout
    assert "eyJ.bot" not in result.stdout + result.stderr

    # A rerun keeps the registration and .env, and backs up the compose files.
    secret = platform_env["PLATFORM_EXTENSION_SECRETS"]
    kept = (om_dir / ".env").read_bytes()
    (om_dir / "compose.yaml").write_text("previous")
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Already registered with the platform." in result.stdout
    assert read(platform / ".env")["PLATFORM_EXTENSION_SECRETS"] == secret
    assert read(om_dir / ".env") == read_bytes_env(kept)
    assert (om_dir / "compose.yaml.bak").read_text() == "previous"
    assert "is running." in result.stdout


def read_bytes_env(data):
    return {k: v for k, _, v in (line.partition("=") for line in data.decode().splitlines())
            if not k.startswith("#") and _}


def test_without_settings_it_installs_but_does_not_start(installer):
    run, env, platform = installer
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (platform / "extensions/openmetadata/.env").is_file()
    assert "is installed, but not started" in result.stdout
    assert "run the install command again with --demo" in result.stdout
    assert not any(call["cwd"] == str(platform / "extensions/openmetadata") for call in compose_ups(env))


def test_demo_starts_a_local_openmetadata(installer):
    run, env, platform = installer
    result = run("--demo")
    assert result.returncode == 0, result.stdout + result.stderr
    om_dir = platform / "extensions/openmetadata"
    settings = read(om_dir / ".env")
    assert settings["COMPOSE_FILE"] == "compose.yaml:compose.openmetadata.yaml"
    assert settings["OM_JWT_TOKEN"] == "eyJ.local-bot"
    token = next(call["args"] for call in calls(env) if call["args"][-1:] == ["http://openmetadata-server:8585/api"])
    assert token[token.index("--network") + 1] == "iceberg-openmetadata_catalog"
    ups = [call["args"] for call in compose_ups(env) if call["cwd"] == str(om_dir)]
    assert ups == [["compose", "up", "-d", "--wait", "openmetadata-server"], ["compose", "up", "-d", "--wait"]]
    assert "Catalog  http://localhost:8585 (admin@open-metadata.org, password admin)" in result.stdout
    # A rerun without --demo keeps the local OpenMetadata.
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "http://localhost:8585" in result.stdout


def test_demo_ignores_openmetadata_settings_in_the_environment(installer):
    run, env, platform = installer
    om_dir = platform / "extensions/openmetadata"
    exported = {"OM_URL": "https://om.example.com/api", "OM_JWT_TOKEN": "eyJ.bot", "OM_UI_URL": "https://om.example.com"}
    # Compose prefers the environment over .env, so the local OpenMetadata's settings must be all it sees.
    for args in (["--demo"], []):
        result = run(*args, **exported)
        assert result.returncode == 0, result.stdout + result.stderr
        settings = read(om_dir / ".env")
        assert (settings["OM_URL"], settings["OM_UI_URL"]) == ("", "")
        assert settings["OM_JWT_TOKEN"] == "eyJ.local-bot"
        ups = [call for call in compose_ups(env) if call["cwd"] == str(om_dir)]
        assert ups and all(call["env"] == [] for call in ups)
        assert "Catalog  http://localhost:8585" in result.stdout


def test_a_checksum_failure_installs_nothing(installer):
    run, env, platform = installer
    sums = Path(env["TEST_RELEASE"]) / "SHA256SUMS"
    sums.write_text("0" * 64 + sums.read_text()[64:])
    result = run()
    assert result.returncode != 0
    assert "checksum failed" in result.stderr
    assert not (platform / "extensions").exists()


def test_it_needs_an_installed_platform(installer, tmp_path):
    run, env, platform = installer
    (platform / ".git").mkdir()
    result = run()
    assert result.returncode != 0 and "source checkout" in result.stderr
    (platform / ".git").rmdir()
    (platform / "compose.users.yaml").unlink()
    result = run()
    assert result.returncode != 0 and "No installed platform" in result.stderr
    assert not [call for call in calls(env) if call["args"][0] == "run" or "up" in call["args"]]


def test_only_a_released_installer_runs(installer):
    run, _, _ = installer
    result = run(source=(ROOT / "install.sh").read_text())
    assert result.returncode != 0 and "install.sh of an OpenMetadata release" in result.stderr
