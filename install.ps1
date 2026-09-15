# Install the latest released Iceberg Data Platform with Docker Desktop.
& {
    $ErrorActionPreference = 'Stop'
    $InstallDir = if ($env:ICEBERG_INSTALL_DIR) { $env:ICEBERG_INSTALL_DIR } else { Join-Path $HOME 'iceberg-data-platform' }
    $ReleaseUrl = 'https://github.com/sanderdw/iceberg-data-platform/releases'
    $TemporaryDir = Join-Path ([IO.Path]::GetTempPath()) ('iceberg-install-' + [guid]::NewGuid().ToString('N'))

    function Invoke-Docker {
        & docker @args
        if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). Check the output above." }
    }
    function Invoke-AdminCompose {
        Invoke-Docker compose --project-name iceberg-platform --env-file "$InstallDir/.env" -f "$InstallDir/compose.yaml" @args
    }
    function Invoke-UsersCompose {
        Invoke-Docker compose --project-name iceberg-workspaces --env-file "$InstallDir/.env" -f "$InstallDir/compose.users.yaml" @args
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
        Write-Host 'Downloading the latest release...'
        # TLS 1.2 also supports Windows PowerShell 5.1 on older system defaults.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing "$ReleaseUrl/latest/download/SHA256SUMS" -OutFile "$TemporaryDir/SHA256SUMS"
        $Entries = @(Get-Content "$TemporaryDir/SHA256SUMS" | ForEach-Object {
            if ($_ -match '^([0-9a-f]{64})  (iceberg-data-platform-([0-9]+\.[0-9]+\.[0-9]+)-install\.tar\.gz)$') {
                [PSCustomObject]@{ Hash = $Matches[1]; Archive = $Matches[2]; Version = $Matches[3] }
            }
        })
        if ($Entries.Count -ne 1) { throw 'The latest release must contain exactly one installation bundle.' }
        $Entry = $Entries[0]
        $ArchivePath = Join-Path $TemporaryDir $Entry.Archive
        Invoke-WebRequest -UseBasicParsing "$ReleaseUrl/download/v$($Entry.Version)/$($Entry.Archive)" -OutFile $ArchivePath
        if ((Get-FileHash $ArchivePath -Algorithm SHA256).Hash -ne $Entry.Hash) { throw 'Installation bundle checksum failed.' }
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
        Invoke-Docker pull ghcr.io/sanderdw/iceberg-data-platform-portal:latest
        Invoke-Docker run --rm --mount "type=bind,src=$InstallDir,dst=/install" `
            --entrypoint python ghcr.io/sanderdw/iceberg-data-platform-portal:latest /install/scripts/setup.py

        Write-Host 'Pulling images for both stacks (the notebook image may take a few minutes)...'
        Invoke-AdminCompose pull
        Invoke-UsersCompose --profile images pull
        Write-Host 'Starting administration and data services...'
        Invoke-AdminCompose up -d --no-build --wait --wait-timeout 300
        Write-Host 'Starting the user portal...'
        Invoke-UsersCompose up -d --no-build --wait --wait-timeout 300 users
        Write-Host "`nIceberg Data Platform is ready."
        Write-Host 'Administration: http://localhost:3000'
        Write-Host 'User portal:    http://localhost:3002'
        Write-Host "Password: PORTAL_PASSWORD in $InstallDir/.env"
        Write-Host "Configuration: $InstallDir"
    }
    finally {
        if (Test-Path $TemporaryDir) { Remove-Item $TemporaryDir -Recurse -Force }
    }
}
