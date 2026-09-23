#!/bin/sh
# Install a released Iceberg Data Platform with Docker Compose.
set -eu

main() {
    install_dir=${ICEBERG_INSTALL_DIR:-"$HOME/iceberg-data-platform"}
    case $# in
        0) ;;
        1)
            if [ "$1" = --help ]; then
                printf 'Usage: sh install.sh [--dir DIRECTORY]\nDefault: ~/iceberg-data-platform (or ICEBERG_INSTALL_DIR)\nRequires Linux or macOS, Docker with Compose v2, curl and tar.\n'
                return
            fi
            fail 'Expected --dir DIRECTORY; use --help for usage.'
            ;;
        2)
            [ "$1" = --dir ] || fail 'Expected --dir DIRECTORY.'
            install_dir=$2
            ;;
        *) fail 'Expected --dir DIRECTORY; use --help for usage.' ;;
    esac
    [ -n "$install_dir" ] || fail 'Installation directory must not be empty.'
    case $(uname -s) in
        Linux|Darwin) ;;
        *) fail 'Use install.ps1 in PowerShell on Windows.' ;;
    esac
    if command -v sha256sum >/dev/null 2>&1; then
        checksum_command=sha256sum
    elif command -v shasum >/dev/null 2>&1; then
        checksum_command=shasum
    else
        fail 'Install sha256sum or shasum first.'
    fi
    for command in curl docker tar; do
        command -v "$command" >/dev/null 2>&1 || fail "Required command is missing: $command"
    done
    docker compose version >/dev/null 2>&1 || fail 'Install Docker Compose v2 first.'
    container_os=$(docker info --format '{{.OSType}}') || fail 'Docker is not running or your user cannot access it.'
    [ "$container_os" = linux ] || fail 'Docker must be running Linux containers.'
    if [ -e "$install_dir/.git" ] || [ -e "$install_dir/Dockerfile" ]; then
        fail 'The target is a source checkout. Choose a separate directory with --dir.'
    fi

    umask 077
    temporary_dir=$(mktemp -d)
    trap 'rm -rf "$temporary_dir"' 0
    trap 'exit 1' HUP INT TERM
    release_url=https://github.com/sanderdw/iceberg-data-platform/releases
    release_tag=latest
    setup_image=ghcr.io/sanderdw/iceberg-data-platform-portal:latest
    if [ "$release_tag" = latest ]; then
        download_url=$release_url/latest/download
    else
        download_url=$release_url/download/$release_tag
    fi
    printf 'Downloading release %s...\n' "$release_tag"
    curl -fsSL "$download_url/SHA256SUMS" -o "$temporary_dir/SHA256SUMS"
    # Read only the versioned installation archive, then download from that exact
    # release so a concurrent publication cannot mix the archive and checksum.
    archive=$(awk '$2 ~ /^iceberg-data-platform-[0-9]+\.[0-9]+\.[0-9]+-install\.tar\.gz$/ {print $2}' "$temporary_dir/SHA256SUMS")
    [ -n "$archive" ] || fail 'The selected release has no installation bundle.'
    [ "$(printf '%s\n' "$archive" | wc -l)" -eq 1 ] || fail 'The release has multiple installation bundles.'
    version=${archive#iceberg-data-platform-}
    version=${version%-install.tar.gz}
    if [ "$release_tag" = latest ]; then
        download_url=$release_url/download/v$version
    fi
    curl -fsSL "$download_url/$archive" -o "$temporary_dir/$archive"
    awk -v archive="$archive" '$2 == archive' "$temporary_dir/SHA256SUMS" > "$temporary_dir/install.sha256"
    (cd "$temporary_dir" && verify_checksum) || fail 'Installation bundle checksum failed.'
    mkdir "$temporary_dir/bundle"
    tar -xzf "$temporary_dir/$archive" -C "$temporary_dir/bundle" --strip-components=1
    bundle_dir=$temporary_dir/bundle
    for file in compose.yaml compose.users.yaml .env.example scripts/setup.py pgadmin/servers.json; do
        [ -f "$bundle_dir/$file" ] || fail "Installation bundle is missing $file"
    done

    mkdir -p "$install_dir"
    install_dir=$(cd "$install_dir" && pwd)
    # Back up generated configuration when rerunning; never replace .env.
    for file in compose.yaml compose.users.yaml compose.lan.yaml compose.users.lan.yaml; do
        if [ -f "$install_dir/$file" ]; then
            cp "$install_dir/$file" "$install_dir/$file.bak"
        fi
    done
    cp -R "$bundle_dir/." "$install_dir/"
    cd "$install_dir"
    printf 'Preparing credentials in %s/.env...\n' "$install_dir"
    docker pull "$setup_image"
    docker run --rm --user "$(id -u):$(id -g)" \
        --mount "type=bind,src=$install_dir,dst=/install" \
        --entrypoint python "$setup_image" \
        /install/scripts/setup.py

    printf 'Pulling images for both stacks (the notebook image may take a few minutes)...\n'
    admin_compose pull
    users_compose --profile images pull
    printf 'Starting administration and data services...\n'
    admin_compose up -d --no-build --wait --wait-timeout 300
    printf 'Starting the user portal...\n'
    users_compose up -d --no-build --wait --wait-timeout 300 users
    shown_dir=$install_dir
    if [ -n "${HOME:-}" ] && [ "$HOME" != / ]; then
        case $install_dir in "$HOME"/*) shown_dir="~/${install_dir#"$HOME"/}" ;; esac
    fi
    # cd expands ~ only unquoted; quote the full path when it needs quoting.
    case $shown_dir in
        *[!A-Za-z0-9_./~-]*) cd_dir="\"$install_dir\"" ;;
        *) cd_dir=$shown_dir ;;
    esac
    printf '\nIceberg Data Platform is ready.\nAdministration: http://localhost:3000\nUser portal:    http://localhost:3002\nKeycloak:       http://localhost:8080\nLogin: PLATFORM_ADMIN_USERNAME and initial PLATFORM_ADMIN_PASSWORD in %s/.env\nConfiguration: %s\n' "$shown_dir" "$shown_dir"
    printf '\nStop:  cd %s && docker compose -f compose.users.yaml down && docker compose down\n' "$cd_dir"
    printf 'Start: cd %s && docker compose up -d --wait && docker compose -f compose.users.yaml up -d --wait users\n' "$cd_dir"
    printf 'Your own tools: cd %s && uv run iceberg_connect.py login\n' "$cd_dir"
    printf '\nGetting started:\n  Administration: http://localhost:3000/#guide\n  User portal:    http://localhost:3002/#guide\n'
    printf '\nAgent skills for Codex, GitHub Copilot, Claude Code and other coding agents, in %s/.agents/skills:\n' "$shown_dir"
    printf '  lan-access    Open the portals to other devices on your network (HTTPS)\n'
    printf '  demo-company  Set up an Energy, Webshop or Retail demo company for a class, one account per participant\n'
    printf 'Start your coding agent in %s and ask it to use one of these skills.\n' "$shown_dir"
    printf 'Claude Code reads .claude/skills only: ask it to follow .agents/skills/<name>/SKILL.md.\n'
}

verify_checksum() {
    if [ "$checksum_command" = sha256sum ]; then
        sha256sum --check install.sha256
    else
        shasum -a 256 --check install.sha256
    fi
}

fail() {
    printf 'Installation failed: %s\n' "$*" >&2
    exit 1
}

admin_compose() {
    docker compose --env-file "$install_dir/.env" -f "$install_dir/compose.yaml" "$@"
}

users_compose() {
    docker compose --env-file "$install_dir/.env" -f "$install_dir/compose.users.yaml" "$@"
}

# Keep execution last so a truncated download cannot start a partial install.
main "$@"
