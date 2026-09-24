"""Exercise downloaded installers without touching the host Docker daemon."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.release import installer_source

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = os.environ.get("ICEBERG_TEST_POWERSHELL") or shutil.which(
    "powershell" if sys.platform == "win32" else "pwsh"
)


@pytest.fixture(params=[("latest", "latest"), ("keycloak-123-1", "keycloak-123-1"), ("v0.2.1", "0.2.1")],
                ids=lambda tags: tags[0])
def installer_env(tmp_path, request):
    release = tmp_path / "release"
    release.mkdir()
    archive_name = "iceberg-data-platform-0.2.1-install.tar.gz"
    archive = release / archive_name
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in {
            "compose.yaml": b"name: iceberg-platform\n",
            "compose.users.yaml": b"name: iceberg-workspaces\n",
            "pgadmin/servers.json": b"{}",
            ".env.example": b"PORTAL_PASSWORD=replace-with-generated-password\n",
            "scripts/setup.py": (ROOT / "scripts/setup.py").read_bytes(),
            "iceberg_connect.py": b"# helper\n",
            ".agents/skills/quick-share/SKILL.md": b"---\nname: quick-share\n---\n",
            ".agents/skills/demo-company/SKILL.md": b"---\nname: demo-company\n---\n",
        }.items():
            info = tarfile.TarInfo("iceberg-data-platform-0.2.1-install/" + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  {archive_name}\n")
    env = os.environ | {
        "ICEBERG_INSTALL_DIR": str(tmp_path / "install with spaces"),
        "TEST_RELEASE": str(release),
        "TEST_DOCKER_LOG": str(tmp_path / "docker.jsonl"),
        "TEST_DOWNLOAD_LOG": str(tmp_path / "downloads.txt"),
        "TEST_RELEASE_TAG": request.param[0],
        "TEST_IMAGE_TAG": request.param[1],
        "TEST_PYTHON": sys.executable,
        "TEST_FAIL": "",
    }
    return env


def calls(env):
    return [json.loads(line) for line in Path(env["TEST_DOCKER_LOG"]).read_text().splitlines()]


def assert_started(env):
    commands = calls(env)
    tag = env["TEST_RELEASE_TAG"]
    setup_image = "ghcr.io/sanderdw/iceberg-data-platform-portal:" + env["TEST_IMAGE_TAG"]
    assert ["pull", setup_image] in commands
    assert any(args[0] == "run" and setup_image in args for args in commands)
    downloads = Path(env["TEST_DOWNLOAD_LOG"]).read_text().splitlines()
    base = "https://github.com/sanderdw/iceberg-data-platform/releases/"
    prefix = "latest/download/" if tag == "latest" else f"download/{tag}/"
    archive_prefix = "download/v0.2.1/" if tag == "latest" else prefix
    assert downloads == [base + prefix + "SHA256SUMS", base + archive_prefix + "iceberg-data-platform-0.2.1-install.tar.gz"]
    pulls = [i for i, args in enumerate(commands) if args[-1] == "pull"]
    starts = [(i, args) for i, args in enumerate(commands) if "up" in args]
    assert len(starts) == 2
    assert pulls and max(pulls) < starts[0][0]
    assert Path(starts[0][1][starts[0][1].index("-f") + 1]).name == "compose.yaml"
    assert Path(starts[1][1][starts[1][1].index("-f") + 1]).name == "compose.users.yaml"
    assert starts[1][1][-1] == "users"
    assert any("images" in args and args[-1] == "pull" for args in commands)
    assert all("--wait" in args and "--no-build" in args for _, args in starts)
    assert not any("down" in args for args in commands)
    target = Path(env["ICEBERG_INSTALL_DIR"])
    assert (target / ".env").read_text().startswith("PORTAL_PASSWORD=")
    assert "replace-with-generated" not in (target / ".env").read_text()
    assert (target / "pgadmin/servers.json").is_file()


@pytest.fixture
def shell_installer(installer_env, tmp_path):
    if sys.platform == "win32":
        pytest.skip("Use the native PowerShell installer on Windows")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    programs = {
        "curl": '''import os, pathlib, shutil, sys
args = sys.argv[1:]
url = next(arg for arg in args if arg.startswith('https:'))
with open(os.environ['TEST_DOWNLOAD_LOG'], 'a') as log:
    log.write(url + '\\n')
shutil.copyfile(pathlib.Path(os.environ['TEST_RELEASE']) / url.rsplit('/', 1)[1], args[args.index('-o') + 1])
''',
        "docker": '''import json, os, pathlib, runpy, sys
args = sys.argv[1:]
with open(os.environ['TEST_DOCKER_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if os.environ['TEST_FAIL'] and os.environ['TEST_FAIL'] in args:
    sys.exit(1)
if args[0] == 'info':
    print('linux')
if args[0] == 'run':
    runpy.run_path(str(pathlib.Path(os.environ['ICEBERG_INSTALL_DIR']) / 'scripts/setup.py'))
''',
    }
    for name, source in programs.items():
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
    env = installer_env | {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}

    def run():
        # Pipe source into sh, exactly as the documented curl command does.
        source = installer_source(ROOT, "install.sh", env["TEST_RELEASE_TAG"], env["TEST_IMAGE_TAG"]).decode()
        return subprocess.run(["sh"], input=source, env=env, text=True, capture_output=True, check=False)

    return run, env


def assert_next_steps(output, target):
    # Success names the version, then the first sign-in with the temporary password from the new .env.
    password = next(line.split("=", 1)[1] for line in (target / ".env").read_text().splitlines()
                    if line.startswith("PLATFORM_ADMIN_PASSWORD="))
    assert "\nIceberg Data Platform 0.2.1 is running.\n" in output
    assert "1. Sign in to the administration portal\n   http://localhost:3000/#guide\n" in output
    assert "   Username  platform-admin\n" in output
    assert f"   Password  {password}  (temporary: you choose a new one at first sign-in)\n" in output
    assert "   http://localhost:3002/#guide\n   Or skip the setup: the demo-company skill below" in output
    # Keycloak needs no manual visit, so the output leaves it out.
    assert "8080" not in output.split("is running.")[1]
    # The installation folder holds the helper and the commands to start and stop both stacks.
    assert (target / "iceberg_connect.py").read_bytes() == b"# helper\n"
    assert "docker compose -f compose.users.yaml down" in output
    assert "docker compose -f compose.users.yaml up -d --wait users" in output
    assert "uv run iceberg_connect.py login" in output
    assert f'"{target}"' in output
    # The installer points to the bundled agent skills and copies the dot-directory.
    assert (target / ".agents/skills/demo-company/SKILL.md").is_file()
    assert "quick-share    Share the portals for a class or demo over temporary HTTPS links" in output
    assert "demo-company   Create an Energy, Webshop or Retail demo company, one account per participant" in output
    assert output.rstrip().endswith('and ask, for example: "Set up a demo company for my class"')


def assert_rerun_hides_password(output, target):
    # After the first sign-in the password in .env is stale, so a rerun only points to it.
    assert "Password  the one you chose at first sign-in (initial: PLATFORM_ADMIN_PASSWORD in " in output
    assert "/.env)\n" in output
    password = next(line.split("=", 1)[1] for line in (target / ".env").read_text().splitlines()
                    if line.startswith("PLATFORM_ADMIN_PASSWORD="))
    assert password not in output


def test_shell_install_and_rerun_preserve_configuration(shell_installer):
    run, env = shell_installer
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert_started(env)
    target = Path(env["ICEBERG_INSTALL_DIR"])
    assert_next_steps(result.stdout, target)
    original = (target / ".env").read_bytes()
    assert (target / ".env").stat().st_mode & 0o777 == 0o600
    (target / "compose.yaml").write_text("previous config")
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert_rerun_hides_password(result.stdout, target)
    assert (target / ".env").read_bytes() == original
    assert (target / "compose.yaml.bak").read_text() == "previous config"


def test_shell_shows_a_home_installation_with_tilde(shell_installer, tmp_path):
    run, env = shell_installer
    env["HOME"] = str(tmp_path)
    env["ICEBERG_INSTALL_DIR"] = str(tmp_path / "iceberg-data-platform")
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "iceberg-data-platform" / "iceberg_connect.py").is_file()
    assert "Installed in ~/iceberg-data-platform\n" in result.stdout
    # Unquoted, so the shell expands ~.
    assert "Stop     cd ~/iceberg-data-platform && " in result.stdout
    assert "Open your agent in ~/iceberg-data-platform and ask" in result.stdout
    assert str(tmp_path) not in result.stdout.split("is running.")[1]


def test_shell_checksum_failure_does_not_install(shell_installer):
    run, env = shell_installer
    archive = next(Path(env["TEST_RELEASE"]).glob("*.tar.gz"))
    archive.write_bytes(b"bad download")
    result = run()
    assert result.returncode != 0
    assert "checksum failed" in result.stdout + result.stderr
    assert not Path(env["ICEBERG_INSTALL_DIR"]).exists()
    assert not any("up" in args or "run" in args for args in calls(env))


def test_shell_pull_failure_does_not_start_stacks(shell_installer):
    run, env = shell_installer
    env["TEST_FAIL"] = "pull"
    assert run().returncode != 0
    assert any("pull" in args for args in calls(env))
    assert not any("up" in args for args in calls(env))


@pytest.fixture
def powershell_installer(installer_env, tmp_path):
    if not POWERSHELL:
        pytest.skip("PowerShell is not available")
    harness = tmp_path / "harness.ps1"
    harness.write_text(r'''
$ErrorActionPreference = 'Stop'
function docker {
    ConvertTo-Json -InputObject @($args) -Compress | Add-Content $env:TEST_DOCKER_LOG
    $global:LASTEXITCODE = 0
    if ($env:TEST_FAIL -and $args -contains $env:TEST_FAIL) { $global:LASTEXITCODE = 1; return }
    if ($args[0] -eq 'info') { 'linux' }
    if ($args[0] -eq 'run') { & $env:TEST_PYTHON "$env:ICEBERG_INSTALL_DIR/scripts/setup.py" }
}
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    Add-Content $env:TEST_DOWNLOAD_LOG $Uri
    Copy-Item (Join-Path $env:TEST_RELEASE ($Uri.Split('/')[-1])) $OutFile
}
try {
    # Exercise the documented Invoke-Expression entrypoint, including scope.
    Get-Content -Raw $env:TEST_INSTALLER | Invoke-Expression
} catch {
    Write-Output $_
    exit 1
}
''')
    script = tmp_path / "install.ps1"
    script.write_bytes(installer_source(ROOT, "install.ps1", installer_env["TEST_RELEASE_TAG"],
                                        installer_env["TEST_IMAGE_TAG"]))
    env = installer_env | {"TEST_INSTALLER": str(script)}

    def run():
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                              env=env, text=True, capture_output=True, check=False)

    return run, env


def test_powershell_install_and_rerun_preserve_configuration(powershell_installer):
    run, env = powershell_installer
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert_started(env)
    target = Path(env["ICEBERG_INSTALL_DIR"])
    assert_next_steps(result.stdout, target)
    original = (target / ".env").read_bytes()
    (target / "compose.yaml").write_text("previous config")
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert_rerun_hides_password(result.stdout, target)
    assert (target / ".env").read_bytes() == original
    assert (target / "compose.yaml.bak").read_text() == "previous config"


def test_powershell_checksum_failure_does_not_install(powershell_installer):
    run, env = powershell_installer
    archive = next(Path(env["TEST_RELEASE"]).glob("*.tar.gz"))
    archive.write_bytes(b"bad download")
    result = run()
    assert result.returncode != 0
    assert "checksum failed" in result.stdout + result.stderr
    assert not Path(env["ICEBERG_INSTALL_DIR"]).exists()
    assert not any("up" in args or "run" in args for args in calls(env))


def test_powershell_pull_failure_does_not_start_stacks(powershell_installer):
    run, env = powershell_installer
    env["TEST_FAIL"] = "pull"
    assert run().returncode != 0
    assert any("pull" in args for args in calls(env))
    assert not any("up" in args for args in calls(env))
