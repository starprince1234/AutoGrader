# Launch one backend module's own process (worker, event relay, consumer) on this machine.
#
# The final integration sequence needs three of these -- B2's evaluation worker, B3's
# outbox relay + statistics consumer, B4's messaging relay -- and until this script existed
# the sequence said "start them" without a runnable way to do it. `run_host.ps1` covers the
# composition host; this covers everything that deliberately runs *outside* it.
#
# A module process reads *unprefixed* variable names (`DATABASE_URL`, `NATS_URL`) because each
# module must stay independently runnable. `.env.integration` is namespaced, so this script
# performs the same re-exposure the composition host does in `module_settings()` (ADR-0008):
# every global variable, then `<MODULE>_`-prefixed ones **overriding** under their bare names.
# The override direction is load-bearing: with `setdefault` the platform superuser DSN stays
# visible and B4's least-privilege guard correctly refuses to start.
#
# Usage:
#   pwsh -File MAIN/scripts/run_module_process.ps1 -Module B2 -Entry autograder_b2.worker
#   pwsh -File MAIN/scripts/run_module_process.ps1 -Module B3 -Entry autograder_b3.consumer_cli

param(
    [Parameter(Mandatory = $true)][ValidatePattern('^B[0-9]{1,3}$')][string]$Module,
    [Parameter(Mandatory = $true)][string]$Entry,
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"

$mainRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $mainRoot ".env.integration"
if (-not (Test-Path $envFile)) {
    throw "missing $envFile (the integration environment drives module processes too)"
}

$values = [ordered]@{}
foreach ($raw in (Get-Content -Encoding utf8 $envFile)) {
    $line = $raw.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { continue }
    $name, $value = $line.Split("=", 2)
    $values[$name.Trim()] = $value.Trim()
}

# Every variable as written, then this module's prefixed ones re-exposed under their bare
# names **overriding** the globals. This mirrors `module_settings()` in the composition host
# exactly, and the first pass is not optional: modules read a mixture of both names (B2 reads
# `B2_ARTIFACT_ROOT` and `DATABASE_URL`), so a script that only translated one way would run
# the same module with a *different* configuration depending on whether it was hosted or not.
foreach ($name in $values.Keys) {
    [Environment]::SetEnvironmentVariable($name, $values[$name], "Process")
}
$prefix = "${Module}_"
foreach ($name in $values.Keys) {
    if ($name.StartsWith($prefix)) {
        [Environment]::SetEnvironmentVariable($name.Substring($prefix.Length), $values[$name], "Process")
    }
}

$python = Join-Path $mainRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "missing $python (run: uv sync --all-groups --extra modules)" }

# Python writes its logs as UTF-8 JSON; without this the console renders Chinese detail as
# mojibake and an operator reads a corrupted reason.
$env:PYTHONIOENCODING = "utf-8"

Write-Host "starting $Module process: python -m $Entry $($Arguments -join ' ')" -ForegroundColor Cyan
& $python -m $Entry @Arguments
