param(
    [switch]$NoBuild,
    [ValidatePattern('^[a-z][a-z0-9-]{1,48}$')][string]$ProjectName = 'ba-m01-compose',
    [ValidateRange(1,65535)][int]$AppPort = 443,
    [ValidateRange(1,65535)][int]$IdpPort = 9443
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$target = 'secops@192.168.10.22'
$defaultProject = $ProjectName -eq 'ba-m01-compose'
$remote = if ($defaultProject) { '/home/secops/business-attribution/compose' } else { "/home/secops/business-attribution/$ProjectName" }
$package = Join-Path $root $(if ($defaultProject) { '.run/compose-package' } else { ".run/$ProjectName" })
New-Item -ItemType Directory -Force $package | Out-Null
if (-not $NoBuild) {
    if (-not (Test-Path frontend/node_modules)) {
        & npm ci --prefix frontend
        if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed' }
    }
    & npm run build --prefix frontend
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
$env:PYTHONPATH = Join-Path $root 'backend'
$env:BA_PREPARE_ROOT = $package
try { & uv run --locked python -m app.bootstrap } finally { Remove-Item Env:BA_PREPARE_ROOT -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw 'Compose credential preparation failed' }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls (Join-Path $package '.secrets') /inheritance:r /grant:r ('{0}:(OI)(CI)F' -f $identity) 'SYSTEM:(OI)(CI)F' | Out-Null
& icacls (Join-Path $package '.env') /inheritance:r /grant:r ('{0}:F' -f $identity) 'SYSTEM:F' | Out-Null
$archive = Join-Path $package 'source.tar'
& tar -cf $archive --exclude=__pycache__ --exclude=tests backend frontend/dist frontend/public/auth.css frontend/public/fonts scripts/container-entry.py Dockerfile Dockerfile.gateway Caddyfile compose.yml pyproject.toml uv.lock .python-version alembic.ini .dockerignore .tools/caddy-linux/caddy
if ($LASTEXITCODE -ne 0) { throw 'Source packaging failed' }
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "mkdir -p $remote/.secrets && chmod 700 $remote/.secrets"
if ($LASTEXITCODE -ne 0) { throw 'Remote directory preparation failed' }
& scp -q $archive "${target}:$remote/source.tar"
if ($LASTEXITCODE -ne 0) { throw 'Source transfer failed' }
$publicUrl = if ($AppPort -eq 443) { 'https://192.168.10.22' } else { "https://192.168.10.22:$AppPort" }
$deployment = "BA_COMPOSE_PROJECT=$ProjectName`nBA_PUBLIC_URL=$publicUrl`nBA_ISSUER_URL=https://192.168.10.22:$IdpPort`nBA_HTTPS_PORT=$AppPort`nBA_IDP_PORT=$IdpPort`n"
[IO.File]::WriteAllText((Join-Path $package 'deployment.env'), $deployment, [Text.UTF8Encoding]::new($false))
& scp -q (Join-Path $package 'deployment.env') "${target}:$remote/.env"
if ($LASTEXITCODE -ne 0) { throw 'Deployment settings transfer failed' }
foreach ($name in @('master.key','oidc-client.secret','idp.pem','demo-accounts.json','postgres.password')) {
    & ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "test -f $remote/.secrets/$name"
    if ($LASTEXITCODE -ne 0) {
        & scp -q (Join-Path $package ".secrets/$name") "${target}:$remote/.secrets/$name"
        if ($LASTEXITCODE -ne 0) { throw 'Credential transfer failed; no values printed' }
    }
}
# The parent is private; bind-mounted Compose secrets must be readable by the unprivileged application UID.
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "chmod 444 $remote/.secrets/* && cd $remote && tar -xf source.tar && docker compose up -d --build --wait"
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed; inspect remote compose status/logs' }
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no $target "cd $remote && docker compose cp gateway:/data/caddy/pki/authorities/local/root.crt ./ca.crt"
if ($LASTEXITCODE -ne 0) { throw 'Compose certificate export failed' }
& scp -q "${target}:$remote/ca.crt" (Join-Path $package 'ca.crt')
Write-Output "Compose application: $publicUrl ; IdP: https://192.168.10.22:$IdpPort"
Write-Output "Public CA and independent test accounts: $package"
