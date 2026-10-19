# Launch the integrated composition host on Windows.
#
# Loads `.env.integration`, then runs uvicorn on the repository's own virtualenv.
# Documented in docs/runbooks/integration.md; see also `.env.integration` for why the
# per-module variables are namespaced.
#
# Usage:  pwsh -File MAIN/scripts/run_host.ps1 [-Port 18000]

param(
    [int]$Port = 18000,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$mainRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $mainRoot ".env.integration"
if (-not (Test-Path $envFile)) {
    throw "missing $envFile (copy .env.integration and fill in local values)"
}

Get-Content -Encoding utf8 $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $name, $value = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
}

# Runtime scratch: artifact store, sandbox workspaces, generated JWKS. Gitignored.
$runtime = Join-Path $mainRoot ".runtime"
foreach ($dir in @($runtime, (Join-Path $runtime "b2-artifacts"), (Join-Path $runtime "b2-workspace"))) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

$python = Join-Path $mainRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "missing $python (run: uv sync --all-groups --extra modules)" }

$uvicornArgs = @("-m", "uvicorn", "autograder_host.main:app", "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) { $uvicornArgs += @("--reload") }

Write-Host "starting composition API on http://127.0.0.1:$Port (start B1 separately with Vite)" -ForegroundColor Cyan
& $python @uvicornArgs
