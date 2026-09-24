# Install a released Iceberg Data Platform with Docker Desktop.
& {
    $ErrorActionPreference = 'Stop'
    $InstallDir = if ($env:ICEBERG_INSTALL_DIR) { $env:ICEBERG_INSTALL_DIR } else { Join-Path $HOME 'iceberg-data-platform' }
    $ReleaseUrl = 'https://github.com/sanderdw/iceberg-data-platform/releases'
    $ReleaseTag = 'latest'
    $SetupImage = 'ghcr.io/sanderdw/iceberg-data-platform-portal:latest'
    $TemporaryDir = Join-Path ([IO.Path]::GetTempPath()) ('iceberg-install-' + [guid]::NewGuid().ToString('N'))

    function Invoke-Docker {
        & docker @args
        if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). Check the output above." }
    }
    function Invoke-AdminCompose {
        Invoke-Docker compose --env-file "$InstallDir/.env" -f "$InstallDir/compose.yaml" @args
    }
    function Invoke-UsersCompose {
        Invoke-Docker compose --env-file "$InstallDir/.env" -f "$InstallDir/compose.users.yaml" @args
    }

    Get-Command docker -ErrorAction Stop | Out-Null
    Get-Command tar -ErrorAction Stop | Out-Null
    Invoke-Docker compose version | Out-Null
    $ContainerOS = Invoke-Docker info --format '{{.OSType}}'
    if ($ContainerOS -ne 'linux') { throw 'Start Docker Desktop and switch to Linux containers first.' }
    if ((Test-Path "$InstallDir/.git") -or (Test-Path "$InstallDir/Dockerfile")) {
        throw 'The target is a source checkout. Set ICEBERG_INSTALL_DIR to a separate directory.'
    }

    try {
        New-Item -ItemType Directory -Path $TemporaryDir | Out-Null
        Write-Host "Downloading release $ReleaseTag..."
        $DownloadUrl = if ($ReleaseTag -eq 'latest') { "$ReleaseUrl/latest/download" } else { "$ReleaseUrl/download/$ReleaseTag" }
        # TLS 1.2 also supports Windows PowerShell 5.1 on older system defaults.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing "$DownloadUrl/SHA256SUMS" -OutFile "$TemporaryDir/SHA256SUMS"
        $Entries = @(Get-Content "$TemporaryDir/SHA256SUMS" | ForEach-Object {
            if ($_ -match '^([0-9a-f]{64})  (iceberg-data-platform-([0-9]+\.[0-9]+\.[0-9]+)-install\.tar\.gz)$') {
                [PSCustomObject]@{ Hash = $Matches[1]; Archive = $Matches[2]; Version = $Matches[3] }
            }
        })
        if ($Entries.Count -ne 1) { throw 'The selected release must contain exactly one installation bundle.' }
        $Entry = $Entries[0]
        $ArchivePath = Join-Path $TemporaryDir $Entry.Archive
        if ($ReleaseTag -eq 'latest') { $DownloadUrl = "$ReleaseUrl/download/v$($Entry.Version)" }
        Invoke-WebRequest -UseBasicParsing "$DownloadUrl/$($Entry.Archive)" -OutFile $ArchivePath
        $Hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $ActualHash = [BitConverter]::ToString($Hasher.ComputeHash([IO.File]::ReadAllBytes($ArchivePath))).Replace('-', '')
        }
        finally { $Hasher.Dispose() }
        if ($ActualHash -ne $Entry.Hash) { throw 'Installation bundle checksum failed.' }
        $BundleDir = Join-Path $TemporaryDir 'bundle'
        New-Item -ItemType Directory -Path $BundleDir | Out-Null
        & tar -xzf $ArchivePath -C $BundleDir --strip-components=1
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the installation bundle.' }
        foreach ($File in @('compose.yaml', 'compose.users.yaml', '.env.example', 'scripts/setup.py', 'pgadmin/servers.json')) {
            if (!(Test-Path "$BundleDir/$File" -PathType Leaf)) { throw "Installation bundle is missing $File" }
        }

        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        $InstallDir = (Resolve-Path $InstallDir).Path
        foreach ($File in @('compose.yaml', 'compose.users.yaml', 'compose.lan.yaml', 'compose.users.lan.yaml')) {
            if (Test-Path "$InstallDir/$File") { Copy-Item "$InstallDir/$File" "$InstallDir/$File.bak" -Force }
        }
        Get-ChildItem -Force $BundleDir | Copy-Item -Destination $InstallDir -Recurse -Force
        # Only a new .env still holds the password the administrator signs in with.
        $NewEnv = !(Test-Path "$InstallDir/.env")
        Write-Host "Preparing credentials in $InstallDir/.env..."
        Invoke-Docker pull $SetupImage
        Invoke-Docker run --rm --mount "type=bind,src=$InstallDir,dst=/install" `
            --entrypoint python $SetupImage /install/scripts/setup.py

        Write-Host 'Pulling images for both stacks (the notebook image may take a few minutes)...'
        Invoke-AdminCompose pull
        Invoke-UsersCompose --profile images pull
        Write-Host 'Starting administration and data services...'
        Invoke-AdminCompose up -d --no-build --wait --wait-timeout 300
        Write-Host 'Starting the user portal...'
        Invoke-UsersCompose up -d --no-build --wait --wait-timeout 300 users
        $ShownDir = $InstallDir
        foreach ($Separator in @('/', '\')) {
            if ($HOME -and $InstallDir.StartsWith("$HOME$Separator")) { $ShownDir = '~' + $InstallDir.Substring($HOME.Length) }
        }
        # Quote the full path when it needs quoting.
        $LocationDir = if ($ShownDir -match '^[\w~./\\:-]+$') { $ShownDir } else { "`"$InstallDir`"" }
        function Get-EnvValue($Key) {
            (Get-Content "$InstallDir/.env" | Where-Object { $_.StartsWith("$Key=") } | Select-Object -Last 1) -replace '^[^=]*=', ''
        }
        $Password = "the one you chose at first sign-in (initial: PLATFORM_ADMIN_PASSWORD in $ShownDir/.env)"
        if ($NewEnv) { $Password = "$(Get-EnvValue PLATFORM_ADMIN_PASSWORD)  (temporary: you choose a new one at first sign-in)" }
        Write-Host "`nIceberg Data Platform $($Entry.Version) is running."
        Write-Host "`n1. Sign in to the administration portal"
        Write-Host '   http://localhost:3000/#guide'
        Write-Host "   Username  $(Get-EnvValue PLATFORM_ADMIN_USERNAME)"
        Write-Host "   Password  $Password"
        Write-Host "`n2. Create a team and a user, then sign in as that user in the user portal"
        Write-Host '   http://localhost:3002/#guide'
        Write-Host '   Or skip the setup: the demo-company skill below creates teams, users and databases for you'
        Write-Host "`nInstalled in $ShownDir"
        Write-Host "   Stop     Set-Location $LocationDir; docker compose -f compose.users.yaml down; docker compose down"
        Write-Host "   Start    Set-Location $LocationDir; docker compose up -d --wait; docker compose -f compose.users.yaml up -d --wait users"
        Write-Host '   Update   run the install command again; your data and .env are kept'
        Write-Host "`nOptional"
        Write-Host '   Connect Python, DuckDB or DBeaver (needs uv):'
        Write-Host "     Set-Location $LocationDir; uv run iceberg_connect.py login"
        Write-Host '   Skills for coding agents such as Claude Code, Codex and GitHub Copilot:'
        Write-Host '     quick-share    Share the portals for a class or demo over temporary HTTPS links'
        Write-Host '     demo-company   Create an Energy, Webshop or Retail demo company, one account per participant'
        Write-Host "   Open your agent in $ShownDir and ask, for example: `"Set up a demo company for my class`""
    }
    finally {
        if (Test-Path $TemporaryDir) { Remove-Item $TemporaryDir -Recurse -Force }
    }
}
