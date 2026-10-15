#!/bin/sh
# Install the OpenMetadata extension into an installed Iceberg Data Platform with Docker Compose.
set -eu

main() {
    platform_dir=${ICEBERG_INSTALL_DIR:-"$HOME/iceberg-data-platform"}
    demo=false
    while [ $# -gt 0 ]; do
        case $1 in
            --help)
                printf 'Usage: sh install.sh [--dir DIRECTORY] [--demo]\n'
                printf 'Adds OpenMetadata to the platform in DIRECTORY, default ~/iceberg-data-platform (or ICEBERG_INSTALL_DIR),\n'
                printf 'in its extensions/openmetadata folder. Requires the platform 0.9.1 or later, Docker with Compose v2, curl and tar.\n'
                printf '\nYour OpenMetadata: OM_URL (its API, such as https://metadata.example.com/api), OM_JWT_TOKEN (a bot'\''s JWT)\n'
                printf 'and optionally OM_UI_URL (where people browse it). In a terminal the installer asks for the missing ones.\n'
                printf '\n--demo   Also start a local OpenMetadata (about 4 GB of memory) at http://localhost:8585\n'
                return
                ;;
            --demo) demo=true ;;
            --dir)
                [ $# -ge 2 ] || fail 'Expected --dir DIRECTORY.'
                platform_dir=$2
                shift
                ;;
            *) fail "Unknown option $1; use --help for usage." ;;
        esac
        shift
    done
    [ -n "$platform_dir" ] || fail 'Platform directory must not be empty.'
    case $(uname -s) in
        Linux|Darwin) ;;
        *) fail 'Run this installer on Linux or macOS, or in WSL on Windows.' ;;
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
    docker info >/dev/null 2>&1 || fail 'Docker is not running or your user cannot access it.'

    [ -d "$platform_dir" ] || fail "No platform in $platform_dir. Install the platform first, or pass --dir."
    platform_dir=$(cd "$platform_dir" && pwd)
    if [ -e "$platform_dir/.git" ] || [ -e "$platform_dir/Dockerfile" ]; then
        fail 'The platform directory is a source checkout. Follow the extension README there instead.'
    fi
    for file in .env compose.yaml compose.users.yaml scripts/setup.py; do
        [ -f "$platform_dir/$file" ] || fail "No installed platform in $platform_dir (missing $file). Install the platform first, or pass --dir."
    done
    grep -q -- '--listing' "$platform_dir/scripts/setup.py" || fail 'This extension needs the platform 0.9.1 or later. Update the platform first.'
    # The platform's own image runs the setup scripts, so the host needs no Python.
    setup_image=$(grep -o 'ghcr\.io/sanderdw/iceberg-data-platform-portal:[A-Za-z0-9_.-]*' "$platform_dir/compose.yaml" | head -n 1)
    [ -n "$setup_image" ] || fail "No platform image found in $platform_dir/compose.yaml."
    om_dir=$platform_dir/extensions/openmetadata

    temporary_dir=$(mktemp -d)
    trap 'rm -rf "$temporary_dir"' EXIT
    release_tag=unreleased
    [ "$release_tag" != unreleased ] || fail 'Use the install.sh of an OpenMetadata release.'
    download_url=https://github.com/sanderdw/iceberg-data-platform/releases/download/$release_tag
    printf 'Downloading OpenMetadata release %s...\n' "$release_tag"
    curl -fsSL "$download_url/SHA256SUMS" -o "$temporary_dir/SHA256SUMS"
    archive=$(awk '$2 ~ /^iceberg-openmetadata-[0-9]+\.[0-9]+\.[0-9]+-install\.tar\.gz$/ {print $2}' "$temporary_dir/SHA256SUMS")
    [ -n "$archive" ] || fail 'The release has no installation bundle.'
    [ "$(printf '%s\n' "$archive" | wc -l)" -eq 1 ] || fail 'The release has multiple installation bundles.'
    version=${archive#iceberg-openmetadata-}
    version=${version%-install.tar.gz}
    curl -fsSL "$download_url/$archive" -o "$temporary_dir/$archive"
    awk -v archive="$archive" '$2 == archive' "$temporary_dir/SHA256SUMS" > "$temporary_dir/install.sha256"
    (cd "$temporary_dir" && verify_checksum) || fail 'Installation bundle checksum failed.'
    mkdir "$temporary_dir/bundle"
    tar -xzf "$temporary_dir/$archive" -C "$temporary_dir/bundle" --strip-components=1
    for file in compose.yaml compose.openmetadata.yaml scripts/setup.py scripts/om_token.py; do
        [ -f "$temporary_dir/bundle/$file" ] || fail "Installation bundle is missing $file"
    done

    mkdir -p "$om_dir"
    # Back up the compose files when rerunning; .env and .env.bridge are never in the bundle.
    for file in compose.yaml compose.openmetadata.yaml; do
        if [ -f "$om_dir/$file" ]; then
            cp "$om_dir/$file" "$om_dir/$file.bak"
        fi
    done
    cp -R "$temporary_dir/bundle/." "$om_dir/"
    printf 'Installed the OpenMetadata extension %s in %s\n' "$version" "$om_dir"

    # Register it with the platform as a listing extension; a rerun keeps its credentials.
    if [ -f "$om_dir/.env.bridge" ] &&
        grep -Eq '^PLATFORM_EXTENSIONS=(.*,)?openmetadata=' "$platform_dir/.env" &&
        grep -Eq '^PLATFORM_LISTING_EXTENSIONS=(.*,)?openmetadata(,|$)' "$platform_dir/.env"; then
        printf 'Already registered with the platform.\n'
    else
        printf 'Registering it with the platform...\n'
        setup_run /install/scripts/setup.py --extension openmetadata --origin http://localhost:3011 \
            --handshake /install/extensions/openmetadata/.env.bridge --listing >/dev/null
    fi
    # Applies a new registration; unchanged services stay as they are.
    printf 'Starting the platform services...\n'
    admin_compose up -d --wait
    users_compose up -d --wait users

    printf 'Preparing %s/.env...\n' "$om_dir"
    om_setup
    started=false
    if [ "$(om_value COMPOSE_FILE)" = compose.yaml:compose.openmetadata.yaml ]; then
        demo=true
        printf 'Starting a local OpenMetadata (the first start takes a few minutes)...\n'
        om_compose up -d --wait openmetadata-server
        docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$om_dir,dst=/extension" \
            --network iceberg-openmetadata_catalog --entrypoint python "$setup_image" \
            /extension/scripts/om_token.py --url http://openmetadata-server:8585/api >/dev/null
    fi
    if [ "$demo" = true ] || { [ -n "$(om_value OM_URL)" ] && [ -n "$(om_value OM_JWT_TOKEN)" ]; }; then
        printf 'Starting the OpenMetadata extension...\n'
        om_compose up -d --wait
        started=true
    fi

    shown_dir=$om_dir
    if [ -n "${HOME:-}" ] && [ "$HOME" != / ]; then
        case $om_dir in "$HOME"/*) shown_dir="~/${om_dir#"$HOME"/}" ;; esac
    fi
    # cd expands ~ only unquoted; quote the full path when it needs quoting.
    case $shown_dir in
        *[!A-Za-z0-9_./~-]*) cd_dir="\"$om_dir\"" ;;
        *) cd_dir=$shown_dir ;;
    esac
    if [ "$started" = false ]; then
        printf '\nThe OpenMetadata extension %s is installed, but not started: it needs your OpenMetadata.\n' "$version"
        printf '   Set      OM_URL and OM_JWT_TOKEN in %s/.env, then\n' "$shown_dir"
        printf '   Start    cd %s && docker compose up -d --wait\n' "$cd_dir"
        printf '   Or try it with a local OpenMetadata: run the install command again with --demo\n'
        return
    fi
    if [ "$demo" = true ]; then
        catalog='http://localhost:8585 (admin@open-metadata.org, password admin)'
    else
        catalog=$(om_value OM_UI_URL)
        catalog=${catalog:-"$(om_value OM_URL) (set OM_UI_URL for the user portal's link)"}
    fi
    printf '\nThe OpenMetadata extension %s is running.\n' "$version"
    printf '   Catalog  %s\n' "$catalog"
    printf '   Status   http://localhost:%s/api/status\n' "$(om_value OM_PORT)"
    printf '\nNext: a team administrator lists a data share in the user portal (Data shares, Listed in the\n'
    printf 'data catalog). It appears in OpenMetadata within a minute.\n'
    printf '\nInstalled in %s\n' "$shown_dir"
    printf '   Change   edit .env there, then: cd %s && docker compose up -d --wait\n' "$cd_dir"
    printf '   Stop     cd %s && docker compose down\n' "$cd_dir"
    printf '   Update   run the install command of a newer release; .env is kept\n'
}

verify_checksum() {
    if [ "$checksum_command" = sha256sum ]; then
        sha256sum --check --quiet install.sha256
    else
        shasum -a 256 --check --quiet install.sha256
    fi
}

om_value() {
    awk -v key="$1" 'index($0, key "=") == 1 {value = substr($0, length(key) + 2)} END {print value}' "$om_dir/.env"
}

has_tty() {
    ( : </dev/tty ) 2>/dev/null
}

setup_run() {
    docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$platform_dir,dst=/install" \
        --entrypoint python "$setup_image" "$@"
}

# The extension's setup takes OM_URL, OM_JWT_TOKEN and OM_UI_URL from the environment (names only: no
# value reaches a command line) or, in a terminal, from its questions.
om_setup() {
    set -- run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$om_dir,dst=/extension" \
        -e OM_URL -e OM_JWT_TOKEN -e OM_UI_URL --entrypoint python
    if [ "$demo" = true ]; then
        docker "$@" "$setup_image" /extension/scripts/setup.py --demo
    elif has_tty; then
        docker "$@" -i -t "$setup_image" /extension/scripts/setup.py </dev/tty
    else
        docker "$@" "$setup_image" /extension/scripts/setup.py
    fi
}

fail() {
    printf 'Installation failed: %s\n' "$*" >&2
    exit 1
}

admin_compose() {
    docker compose --env-file "$platform_dir/.env" -f "$platform_dir/compose.yaml" "$@"
}

users_compose() {
    docker compose --env-file "$platform_dir/.env" -f "$platform_dir/compose.users.yaml" "$@"
}

# From the extension's folder, so Compose reads its .env, including COMPOSE_FILE for the local OpenMetadata.
om_compose() {
    (cd "$om_dir" && docker compose "$@")
}

# Keep execution last so a truncated download cannot start a partial install.
main "$@"
