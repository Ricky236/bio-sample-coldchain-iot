# 生产启动（Windows / 本机验证）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

New-Item -ItemType Directory -Force -Path "data\uploads" | Out-Null

# 加载 .env（main.py 也会再加载一次）
Get-Content ".env" -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $name, $value = $_.Split('=', 2)
  [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
}

$hostName = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$port = if ($env:PORT) { $env:PORT } else { "8000" }
$workers = if ($env:WORKERS) { $env:WORKERS } else { "1" }

Write-Host "Starting coldchain backend on ${hostName}:${port} (workers=${workers})"
& .\.venv\Scripts\uvicorn.exe main:app --host $hostName --port $port --workers $workers
