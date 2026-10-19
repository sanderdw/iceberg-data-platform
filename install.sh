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
                printf '\nExtensions (optional): ICEBERG_EXTENSIONS=conversationalbi,dbt,ingestion names the ones to add. In a\nterminal the installer asks; ICEBERG_EXTENSIONS=none skips the questions.\n'
                printf '\nConversational BI also starts with LLM_MODEL and its key, for example\nLLM_MODEL=anthropic:claude-sonnet-5-5 ANTHROPIC_API_KEY=...\nVariables: %s\n' "$cbi_variables"
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
    # Conversational BI is optional: asked for, already installed, or accepted at the prompt.
    cbi=false
    cbi_registered=false
    case ",${ICEBERG_EXTENSIONS:-}," in *,conversationalbi,*) cbi=true ;; esac
    [ -z "${LLM_MODEL:-}" ] || cbi=true
    if [ -f "$install_dir/conversationalbi/.env.bridge" ] && [ -f "$install_dir/.env" ] &&
        grep -Eq '^PLATFORM_EXTENSIONS=(.*,)?conversationalbi=' "$install_dir/.env"; then
        cbi=true
        cbi_registered=true
    fi
    if [ "$cbi" = false ] && [ -z "${ICEBERG_EXTENSIONS+set}" ] && has_tty; then
        printf 'Add Conversational BI, a chat that answers from your semantic models with an LLM\n(OpenAI, Anthropic, Google Gemini or Amazon Bedrock)? [y/N] ' >/dev/tty
        answer=
        read -r answer </dev/tty || answer=
        case $answer in [Yy]*) cbi=true ;; esac
    fi
    # The dbt extension likewise.
    dbt=false
    dbt_registered=false
    case ",${ICEBERG_EXTENSIONS:-}," in *,dbt,*) dbt=true ;; esac
    if [ -f "$install_dir/dbt/.env.bridge" ] && [ -f "$install_dir/.env" ] &&
        grep -Eq '^PLATFORM_EXTENSIONS=(.*,)?dbt=' "$install_dir/.env"; then
        dbt=true
        dbt_registered=true
    fi
    if [ "$dbt" = false ] && [ -z "${ICEBERG_EXTENSIONS+set}" ] && has_tty; then
        printf 'Add dbt pipelines, where AI agents build and test dbt models on your tables? Its launcher\nuses the Docker socket to start each run in an isolated container. [y/N] ' >/dev/tty
        answer=
        read -r answer </dev/tty || answer=
        case $answer in [Yy]*) dbt=true ;; esac
    fi
    # And the Ingestion (dlt) extension.
    ing=false
    ing_registered=false
    case ",${ICEBERG_EXTENSIONS:-}," in *,ingestion,*) ing=true ;; esac
    if [ -f "$install_dir/ingestion/.env.bridge" ] && [ -f "$install_dir/.env" ] &&
        grep -Eq '^PLATFORM_EXTENSIONS=(.*,)?ingestion=' "$install_dir/.env"; then
        ing=true
        ing_registered=true
    fi
    if [ "$ing" = false ] && [ -z "${ICEBERG_EXTENSIONS+set}" ] && has_tty; then
        printf 'Add Ingestion (dlt), which loads REST APIs, databases and files into your tables? Its launcher\nuses the Docker socket to start each run in an isolated container. [y/N] ' >/dev/tty
        answer=
        read -r answer </dev/tty || answer=
        case $answer in [Yy]*) ing=true ;; esac
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
    for file in compose.yaml compose.users.yaml .env.example scripts/setup.py pgadmin/servers.json \
        conversationalbi/compose.yaml conversationalbi/scripts/setup.py dbt/compose.yaml dbt/scripts/setup.py \
        dbt/iceberg_dbt.py ingestion/compose.yaml ingestion/scripts/setup.py; do
        [ -f "$bundle_dir/$file" ] || fail "Installation bundle is missing $file"
    done

    mkdir -p "$install_dir"
    install_dir=$(cd "$install_dir" && pwd)
    # Back up generated configuration when rerunning; never replace .env.
    for file in compose.yaml compose.users.yaml compose.lan.yaml compose.users.lan.yaml conversationalbi/compose.yaml \
        dbt/compose.yaml ingestion/compose.yaml; do
        if [ -f "$install_dir/$file" ]; then
            cp "$install_dir/$file" "$install_dir/$file.bak"
        fi
    done
    cp -R "$bundle_dir/." "$install_dir/"
    cd "$install_dir"
    # Only a new .env still holds the password the administrator signs in with.
    new_env=true
    [ ! -f .env ] || new_env=false
    printf 'Preparing credentials in %s/.env...\n' "$install_dir"
    docker pull "$setup_image"
    setup_run "$setup_image" /install/scripts/setup.py
    if [ "$cbi" = true ]; then
        if [ "$cbi_registered" = false ]; then
            # Before the platform starts, so it creates the extension's sign-in clients right away.
            setup_run "$setup_image" /install/scripts/setup.py --extension conversationalbi \
                --origin http://localhost:3007 --handshake /install/conversationalbi/.env.bridge >/dev/null
        fi
        printf 'Preparing Conversational BI in %s/conversationalbi...\n' "$install_dir"
        cbi_setup
    fi
    if [ "$dbt" = true ]; then
        if [ "$dbt_registered" = false ]; then
            setup_run "$setup_image" /install/scripts/setup.py --extension dbt \
                --origin http://localhost:3004 --handshake /install/dbt/.env.bridge >/dev/null
        fi
        printf 'Preparing dbt pipelines in %s/dbt...\n' "$install_dir"
        setup_run "$setup_image" /install/dbt/scripts/setup.py >/dev/null
        # dbt commands from a terminal, next to iceberg_connect.py; executable for the dbt MCP server's DBT_PATH.
        cp "$install_dir/dbt/iceberg_dbt.py" "$install_dir/iceberg_dbt.py"
        chmod +x "$install_dir/iceberg_dbt.py"
    fi
    if [ "$ing" = true ]; then
        if [ "$ing_registered" = false ]; then
            setup_run "$setup_image" /install/scripts/setup.py --extension ingestion \
                --origin http://localhost:3008 --handshake /install/ingestion/.env.bridge >/dev/null
        fi
        printf 'Preparing Ingestion (dlt) in %s/ingestion...\n' "$install_dir"
        setup_run "$setup_image" /install/ingestion/scripts/setup.py >/dev/null
    fi

    printf 'Pulling images (the notebook image may take a few minutes)...\n'
    admin_compose pull
    users_compose --profile images pull
    [ "$cbi" = false ] || cbi_compose pull
    # The images profile adds the runner image, which the launcher starts for every run.
    [ "$dbt" = false ] || dbt_compose --profile images pull
    [ "$ing" = false ] || ing_compose --profile images pull
    printf 'Starting administration and data services...\n'
    admin_compose up -d --no-build --wait --wait-timeout 300
    printf 'Starting the user portal...\n'
    users_compose up -d --no-build --wait --wait-timeout 300 users
    if [ "$cbi" = true ]; then
        printf 'Starting Conversational BI...\n'
        cbi_compose up -d --no-build --wait --wait-timeout 300
    fi
    if [ "$dbt" = true ]; then
        printf 'Starting dbt pipelines...\n'
        dbt_compose up -d --no-build --wait --wait-timeout 300
    fi
    if [ "$ing" = true ]; then
        printf 'Starting Ingestion (dlt)...\n'
        ing_compose up -d --no-build --wait --wait-timeout 300
    fi
    shown_dir=$install_dir
    if [ -n "${HOME:-}" ] && [ "$HOME" != / ]; then
        case $install_dir in "$HOME"/*) shown_dir="~/${install_dir#"$HOME"/}" ;; esac
    fi
    # cd expands ~ only unquoted; quote the full path when it needs quoting.
    case $shown_dir in
        *[!A-Za-z0-9_./~-]*) cd_dir="\"$install_dir\"" ;;
        *) cd_dir=$shown_dir ;;
    esac
    if [ "$new_env" = true ]; then
        password="$(env_value PLATFORM_ADMIN_PASSWORD)  (temporary: you choose a new one at first sign-in)"
    else
        password="the one you chose at first sign-in (initial: PLATFORM_ADMIN_PASSWORD in $shown_dir/.env)"
    fi
    printf '\nIceberg Data Platform %s is running.\n' "$version"
    # A kept .env can hold other ports or a quick-share session's public addresses.
    portal_origin=$(env_value PORTAL_ORIGIN)
    user_origin=$(env_value USER_ORIGIN)
    printf '\n1. Sign in to the administration portal\n   %s/#guide\n' "${portal_origin%/}"
    printf '   Username  %s\n   Password  %s\n' "$(env_value PLATFORM_ADMIN_USERNAME)" "$password"
    printf '\n2. Create a team and a user, then sign in as that user in the user portal\n   %s/#guide\n   Or skip the setup: the demo-company skill below creates teams, users and databases for you\n' "${user_origin%/}"
    stop=
    start=
    step=3
    if [ "$cbi" = true ]; then
        model=$(file_value "$install_dir/conversationalbi/llm.env" LLM_MODEL)
        printf '\n%s. Ask questions in Conversational BI\n   %s\n' "$step" "$(file_value "$install_dir/conversationalbi/.env.bridge" EXTENSION_ORIGIN)"
        printf '   A team administrator enables it once per environment: Enable, in the chat\n'
        printf '   Model    %s\n' "${model:-not set: add LLM_MODEL and its key to conversationalbi/llm.env}"
        printf '   Change   edit %s/conversationalbi/llm.env, then\n            cd %s && docker compose -f conversationalbi/compose.yaml up -d --wait\n' "$shown_dir" "$cd_dir"
        stop='docker compose -f conversationalbi/compose.yaml down && '
        start=' && docker compose -f conversationalbi/compose.yaml up -d --wait'
        step=$((step + 1))
    fi
    if [ "$dbt" = true ]; then
        dbt_origin=$(file_value "$install_dir/dbt/.env.bridge" EXTENSION_ORIGIN)
        printf '\n%s. Build pipelines with dbt\n   %s\n' "$step" "$dbt_origin"
        printf '   A team administrator enables it once per environment: ask your agent (enable_environment)\n'
        printf '   Agent    claude mcp add --transport http --client-id ext-dbt-mcp --callback-port 3010 iceberg-dbt %s/mcp\n' "${dbt_origin%/}"
        printf '   Skill    dbt-platform: ask your agent, for example "Build a pipeline for my sales data"\n'
        printf '   Terminal cd %s && uv run iceberg_dbt.py login (dbt commands that run on the platform)\n' "$cd_dir"
        stop="docker compose -f dbt/compose.yaml down && $stop"
        start="$start && docker compose -f dbt/compose.yaml up -d --wait"
        step=$((step + 1))
    fi
    if [ "$ing" = true ]; then
        ing_origin=$(file_value "$install_dir/ingestion/.env.bridge" EXTENSION_ORIGIN)
        printf '\n%s. Load data with Ingestion (dlt)\n   %s\n' "$step" "$ing_origin"
        printf '   A team administrator enables it once per environment: Enable, on the page\n'
        printf '   Agent    claude mcp add --transport http --client-id ext-ingestion-mcp --callback-port 3010 iceberg-ingestion %s/mcp\n' "${ing_origin%/}"
        printf '   Skill    ingestion: ask your agent, for example "Load the orders table from our PostgreSQL"\n'
        stop="docker compose -f ingestion/compose.yaml down && $stop"
        start="$start && docker compose -f ingestion/compose.yaml up -d --wait"
    fi
    printf '\nInstalled in %s\n' "$shown_dir"
    printf '   Stop     cd %s && %sdocker compose -f compose.users.yaml down && docker compose down\n' "$cd_dir" "$stop"
    printf '   Start    cd %s && docker compose up -d --wait && docker compose -f compose.users.yaml up -d --wait users%s\n' "$cd_dir" "$start"
    printf '   Update   run the install command again; your data and .env are kept\n'
    printf '\nOptional\n   Connect Python, DuckDB or DBeaver (needs uv):\n'
    printf '     cd %s && uv run iceberg_connect.py login\n' "$cd_dir"
    printf '   Skills for coding agents such as Claude Code, Codex and GitHub Copilot:\n'
    printf '     quick-share    Share the portals for a class or demo over temporary HTTPS links\n'
    printf '     demo-company   Create an Energy, Webshop or Retail demo company, one account per participant\n'
    printf '     semantic-model Build a semantic model by interview, tested against your own data\n'
    printf '   Open your agent in %s and ask, for example: "Set up a demo company for my class"\n' "$shown_dir"
}

verify_checksum() {
    if [ "$checksum_command" = sha256sum ]; then
        sha256sum --check install.sha256
    else
        shasum -a 256 --check install.sha256
    fi
}

env_value() {
    file_value "$install_dir/.env" "$1"
}

file_value() {
    awk -v key="$2" 'index($0, key "=") == 1 {value = substr($0, length(key) + 2)} END {print value}' "$1"
}

has_tty() {
    ( : </dev/tty ) 2>/dev/null
}

setup_run() {
    docker run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$install_dir,dst=/install" \
        --entrypoint python "$@"
}

# Conversational BI's setup writes llm.env from these variables (names only: no value reaches a
# command line) or, in a terminal, from its questions.
cbi_variables='LLM_MODEL BI_ALLOW_TEST_MODEL GOOGLE_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY OPENAI_BASE_URL AWS_BEARER_TOKEN_BEDROCK AWS_REGION OPENROUTER_API_KEY'

cbi_setup() {
    set -- run --rm --user "$(id -u):$(id -g)" --mount "type=bind,src=$install_dir,dst=/install" --entrypoint python
    for name in $cbi_variables; do
        set -- "$@" -e "$name"
    done
    if has_tty; then
        docker "$@" -i -t "$setup_image" /install/conversationalbi/scripts/setup.py </dev/tty
    else
        docker "$@" "$setup_image" /install/conversationalbi/scripts/setup.py
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

cbi_compose() {
    docker compose --env-file "$install_dir/conversationalbi/.env" -f "$install_dir/conversationalbi/compose.yaml" "$@"
}

dbt_compose() {
    docker compose --env-file "$install_dir/dbt/.env" -f "$install_dir/dbt/compose.yaml" "$@"
}

ing_compose() {
    docker compose --env-file "$install_dir/ingestion/.env" -f "$install_dir/ingestion/compose.yaml" "$@"
}

# Keep execution last so a truncated download cannot start a partial install.
main "$@"
