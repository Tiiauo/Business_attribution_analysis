$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$env:PYTHONPATH = Join-Path $root 'backend'
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force .run | Out-Null
foreach ($port in @(5174,8010,8011,8443,9443)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is occupied. Use scripts/stop-dev.ps1 before restarting this project."
    }
}
function Start-ProjectService($name, $executable, $arguments, $workingDirectory) {
    $process = Start-Process $executable -ArgumentList $arguments -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $root ".run/$name.out.log") -RedirectStandardError (Join-Path $root ".run/$name.err.log")
    $process.Id | Set-Content (Join-Path $root ".run/$name.pid")
}
Start-ProjectService 'caddy' (Join-Path $root '.tools/caddy/caddy.exe') 'run --config Caddyfile.dev --adapter caddyfile' $root
for ($attempt=0; $attempt -lt 40; $attempt++) {
    if (Test-Path .secrets/caddy/pki/authorities/local/root.crt) { break }
    Start-Sleep -Milliseconds 250
}
Copy-Item .secrets/caddy/pki/authorities/local/root.crt .secrets/ca.crt -Force
$certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2((Join-Path $root '.secrets/ca.crt'))
if (-not (Test-Path ("Cert:/CurrentUser/Root/" + $certificate.Thumbprint))) {
    Import-Certificate -FilePath .secrets/ca.crt -CertStoreLocation Cert:/CurrentUser/Root | Out-Null
}
$python = Join-Path $root '.venv/Scripts/python.exe'
Start-ProjectService 'backend' $python '-m uvicorn app.main:app --host 127.0.0.1 --port 8010 --no-access-log' $root
Start-ProjectService 'worker' $python '-m app.worker' $root
Start-ProjectService 'idp' $python '-m uvicorn demo_auth.main:app --host 127.0.0.1 --port 8011 --no-access-log' $root
Start-ProjectService 'frontend' (Get-Command node).Source 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174 --strictPort' (Join-Path $root 'frontend')
for ($attempt=0; $attempt -lt 40; $attempt++) {
    try {
        $application = Invoke-WebRequest https://localhost:8443/health/ready -UseBasicParsing
        $provider = Invoke-WebRequest https://localhost:9443/health/ready -UseBasicParsing
        $page = Invoke-WebRequest https://localhost:8443/login -UseBasicParsing
        if ($application.StatusCode -eq 200 -and $provider.StatusCode -eq 200 -and $page.StatusCode -eq 200) {
            Write-Output 'Ready: https://localhost:8443 ; independent IdP: https://localhost:9443'
            Write-Output 'Synthetic test-account credentials: .secrets/demo-accounts.json (keep private).'
            exit 0
        }
    } catch { Start-Sleep -Milliseconds 500 }
}
throw 'Services did not become ready. Inspect .run/*.err.log; run scripts/stop-dev.ps1 before retrying.'
