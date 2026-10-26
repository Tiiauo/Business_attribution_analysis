$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
if (Get-NetTCPConnection -LocalPort 5174 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Stop the frontend with ./scripts/stop-dev.ps1 before replacing locked dependencies on Windows.'
}
foreach ($command in @('uv','node','npm','ssh','scp','tar')) { Get-Command $command -ErrorAction Stop | Out-Null }
& uv sync --locked --no-python-downloads
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed; use CPython 3.12.' }
$env:PYTHONPATH = Join-Path $root 'backend'
$env:PYTHONUTF8 = '1'
& uv run --locked python -m app.bootstrap
if ($LASTEXITCODE -ne 0) { throw 'Credential preparation failed.' }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls .secrets /inheritance:r /grant:r ('{0}:(OI)(CI)F' -f $identity) 'SYSTEM:(OI)(CI)F' | Out-Null
& icacls .env /inheritance:r /grant:r ('{0}:F' -f $identity) 'SYSTEM:F' | Out-Null
New-Item -ItemType Directory -Force .tools | Out-Null
$manifest = Get-Content -Raw -Encoding UTF8 scripts/caddy-assets.json | ConvertFrom-Json
foreach ($asset in $manifest.assets) {
    $binary = if ($asset.platform -eq 'windows') { '.tools/caddy/caddy.exe' } else { '.tools/caddy-linux/caddy' }
    if ((Test-Path $binary) -and (Get-FileHash $binary -Algorithm SHA256).Hash -eq $asset.binary_sha256) { continue }
    $archive = Join-Path '.tools' $asset.file
    if (-not (Test-Path $archive)) { Invoke-WebRequest $asset.url -OutFile $archive -UseBasicParsing }
    if ((Get-FileHash $archive -Algorithm SHA512).Hash -ne $asset.sha512) { throw 'Caddy official asset checksum mismatch.' }
    if ($asset.platform -eq 'windows') { Expand-Archive $archive -DestinationPath .tools/caddy -Force }
    else { New-Item -ItemType Directory -Force .tools/caddy-linux | Out-Null; & tar -xf $archive -C .tools/caddy-linux caddy }
    if ((Get-FileHash $binary -Algorithm SHA256).Hash -ne $asset.binary_sha256) { throw 'Extracted Caddy binary checksum mismatch.' }
}
$target = 'secops@192.168.10.22'
$remote = '/home/secops/business-attribution'
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "mkdir -p $remote/.secrets && chmod 700 $remote/.secrets"
if ($LASTEXITCODE -ne 0) { throw 'SSH authentication failed. Existing key/agent authentication is required.' }
$remoteHash = & ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "sha256sum $remote/.secrets/postgres.password 2>/dev/null"
if ($LASTEXITCODE -eq 0) {
    if (($remoteHash -split '\s+')[0] -ne (Get-FileHash .secrets/postgres.password -Algorithm SHA256).Hash) {
        throw 'Existing remote database credential differs. Restore the matching local .secrets backup; no credential overwritten.'
    }
} else {
    & scp -q .secrets/postgres.password "${target}:$remote/.secrets/postgres.password"
    if ($LASTEXITCODE -ne 0) { throw 'Database credential transfer failed.' }
}
& scp -q compose.dev.yml "${target}:$remote/compose.dev.yml"
if ($LASTEXITCODE -ne 0) { throw 'Compose file transfer failed.' }
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "chmod 600 $remote/.secrets/postgres.password && cd $remote && docker compose -f compose.dev.yml up -d --wait"
if ($LASTEXITCODE -ne 0) { throw 'Independent development database failed to start.' }
$ports = @(Get-NetTCPConnection -LocalPort 12375,15432 -State Listen -ErrorAction SilentlyContinue)
if ($ports.Count -eq 0) { & ./scripts/docker-tunnel.ps1 }
elseif ($ports.Count -ne 2 -or -not (Test-Path .run/ssh.pid) -or ($ports.OwningProcess | Where-Object { $_ -ne [int](Get-Content .run/ssh.pid) })) {
    throw 'Project tunnel ports are occupied by another process.'
}
& uv run --locked alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed; existing data has been retained.' }
& npm ci --prefix frontend
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
Write-Output 'Preparation complete. Start services with ./scripts/start-dev.ps1.'
