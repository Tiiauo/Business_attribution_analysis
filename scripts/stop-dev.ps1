$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
foreach ($service in @('worker','frontend','backend','idp','caddy')) {
    $file = Join-Path $root ".run/$service.pid"
    if (-not (Test-Path $file)) { continue }
    $servicePid = [int](Get-Content $file)
    $process = Get-CimInstance Win32_Process -Filter ("ProcessId=$servicePid")
    if (-not $process) { continue }
    $expected = @{worker='*-m app.worker*';frontend='*vite*5174*';backend='*app.main:app*8010*';idp='*demo_auth.main:app*8011*';caddy='*Caddyfile.dev*'}[$service]
    if ($process.CommandLine -notlike $expected) { throw "Stored $service PID belongs to a different process; left running." }
    & taskkill /PID $servicePid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop $service." }
}
Write-Output 'Windows application services stopped; PostgreSQL volumes and SSH tunnel retained.'
