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
        Write-Host "`nIceberg Data Platform is ready."
        Write-Host 'Keycloak:       http://localhost:8080  (sign-in service, managed through the administration portal; no need to open it)'
        $ShownDir = $InstallDir
        foreach ($Separator in @('/', '\')) {
            if ($HOME -and $InstallDir.StartsWith("$HOME$Separator")) { $ShownDir = '~' + $InstallDir.Substring($HOME.Length) }
        }
        # Quote the full path when it needs quoting.
        $LocationDir = if ($ShownDir -match '^[\w~./\\:-]+$') { $ShownDir } else { "`"$InstallDir`"" }
        Write-Host "Login: PLATFORM_ADMIN_USERNAME and initial PLATFORM_ADMIN_PASSWORD in $ShownDir/.env"
        Write-Host "Configuration: $ShownDir"
        Write-Host "`nStop:  Set-Location $LocationDir; docker compose -f compose.users.yaml down; docker compose down"
        Write-Host "Start: Set-Location $LocationDir; docker compose up -d --wait; docker compose -f compose.users.yaml up -d --wait users"
        Write-Host "Your own tools: Set-Location $LocationDir; uv run iceberg_connect.py login"
        Write-Host "`nGetting started:"
        Write-Host '  Administration: http://localhost:3000/#guide'
        Write-Host '  User portal:    http://localhost:3002/#guide'
        Write-Host "`nAgent skills for Codex, GitHub Copilot, Claude Code and other coding agents, in $ShownDir/.agents/skills:"
        Write-Host '  quick-share   Open the portals to participants anywhere for a class or demo (temporary HTTPS)'
        Write-Host '  demo-company  Set up an Energy, Webshop or Retail demo company for a class, one account per participant'
        Write-Host "Start your coding agent in $ShownDir and ask it to use one of these skills."
        Write-Host 'Claude Code reads .claude/skills only: ask it to follow .agents/skills/<name>/SKILL.md.'
    }
    finally {
        if (Test-Path $TemporaryDir) { Remove-Item $TemporaryDir -Recurse -Force }
    }
}
