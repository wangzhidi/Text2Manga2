$ErrorActionPreference = 'Stop'

$caddyExe = Join-Path $PSScriptRoot "bin\caddy.exe"
$caddyfile = Join-Path $PSScriptRoot "Caddyfile"

if (-not (Test-Path $caddyExe)) {
  throw "未找到 Caddy 可执行文件：$caddyExe"
}
if (-not (Test-Path $caddyfile)) {
  throw "未找到 Caddyfile：$caddyfile"
}

& $caddyExe run --config $caddyfile
