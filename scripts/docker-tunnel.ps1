param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$run = Join-Path $root '.run'
New-Item -ItemType Directory -Force -Path $run | Out-Null
$arguments = @('-N', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
    '-L', '127.0.0.1:12375:/var/run/docker.sock', '-L', '127.0.0.1:15432:127.0.0.1:15432', 'secops@192.168.10.22')
if ($Foreground) { & ssh @arguments; exit $LASTEXITCODE }
foreach ($port in @(12375, 15432)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is occupied. Check the existing project tunnel; do not replace another process."
    }
}
$process = Start-Process ssh -ArgumentList $arguments -PassThru -WindowStyle Hidden `
    -RedirectStandardError (Join-Path $run 'ssh.err.log') -RedirectStandardOutput (Join-Path $run 'ssh.out.log')
$process.Id | Set-Content (Join-Path $run 'ssh.pid')
Start-Sleep -Seconds 2
if ($process.HasExited) { throw 'SSH tunnel failed; inspect .run/ssh.err.log.' }
Write-Output 'Project tunnel: Docker 127.0.0.1:12375; PostgreSQL 127.0.0.1:15432.'
