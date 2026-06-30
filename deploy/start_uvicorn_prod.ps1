$ErrorActionPreference = 'Stop'

# 生产启动：仅监听本机回环地址，交给 Caddy 对外暴露 443
# 依赖：pip install uvicorn[standard]

$workers = 1

$args = @(
  'web_app:app',
  '--host', '127.0.0.1',
  '--port', '8000',
  '--workers', "$workers",
  '--proxy-headers',
  '--forwarded-allow-ips', '127.0.0.1'
)

# 使用 python -m uvicorn，避免 PATH/脚本包装器差异导致的参数解析问题
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (Test-Path $venvPython) {
  & $venvPython -m uvicorn @args
} else {
  python -m uvicorn @args
}
