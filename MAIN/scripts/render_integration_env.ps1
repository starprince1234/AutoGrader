<#
.SYNOPSIS
Render `.env.integration` from the Doppler project that owns this deployment's configuration.

.DESCRIPTION
`.env.integration` is a **build product**, not a source. It used to be hand-maintained and
gitignored, which meant the configuration lived in exactly one place nobody could review, and
"what does this deployment set?" had no answer other than reading a file on one machine.

What lives in Doppler is the *base* configuration: DSNs, ports, paths, JWT settings, timeouts,
and the three provider bedrock values (`APIKEY`, `BASE_URL`, `MODEL`). What is **rendered** is
the per-module provider block, because B2 and B3 disagree about the shape of the same fact:

    B2_COMMENTARY_BASE_URL  <- BASE_URL   (B2 appends /chat/completions itself)
    LLM_BASE_URL            <- BASE_URL   (B3 appends /v1/responses for the Responses API)

Storing those variables instead of deriving them would put one API key in three places --
the pattern this project has already removed three times (E4's five digest implementations,
E2's two requirement matrices, the fourteen copies of the contract version).

.PARAMETER Project
Doppler project. Defaults to `autograder`.

.PARAMETER Config
Doppler config. Defaults to `dev_personal`.

.PARAMETER OutFile
Where to write. Defaults to `MAIN/.env.integration`.

.EXAMPLE
pwsh -File MAIN/scripts/render_integration_env.ps1
#>
param(
    [string]$Project = "autograder",
    [string]$Config = "dev_personal",
    [string]$OutFile
)

$ErrorActionPreference = "Stop"

$mainRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $mainRoot ".env.integration" }

# --no-file keeps the payload on stdout; --no-exit-on-write-failure tolerates the local
# fallback cache being unwritable (it is a convenience, not a requirement for this call).
$json = doppler secrets download --no-file --format json --project $Project --config $Config --no-exit-on-write-failure
if ($LASTEXITCODE -ne 0) { throw "doppler secrets download failed (exit $LASTEXITCODE)" }
$secrets = $json | ConvertFrom-Json

foreach ($required in @("APIKEY", "BASE_URL", "MODEL")) {
    if (-not $secrets.$required) { throw "Doppler is missing $required; the provider block cannot be rendered" }
}

$bedrock = @("APIKEY", "BASE_URL", "MODEL")
$derived = [ordered]@{
    "B2_COMMENTARY_PROVIDER"     = "deepseek"
    "B2_COMMENTARY_BASE_URL"     = $secrets.BASE_URL
    "B2_COMMENTARY_MODEL"        = $secrets.MODEL
    "B2_COMMENTARY_API_KEY"      = $secrets.APIKEY
    "LLM_BASE_URL"               = $secrets.BASE_URL
    "LLM_MODEL_NAME"             = $secrets.MODEL
    "LLM_APIKEY"                 = $secrets.APIKEY
}

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# Generated from Doppler project '$Project' / config '$Config'.")
$lines.Add("# Render: pwsh -File MAIN/scripts/render_integration_env.ps1")
$lines.Add("# Gitignored build product -- change the secret in Doppler, not this file.")
$lines.Add("")

$baseCount = 0
foreach ($name in ($secrets.PSObject.Properties.Name | Sort-Object)) {
    if ($bedrock -contains $name) { continue }
    if ($name -like "DOPPLER_*") { continue }
    $lines.Add("$name=$($secrets.$name)")
    $baseCount++
}

$lines.Add("")
$lines.Add("# Provider block, rendered from APIKEY / BASE_URL / MODEL above.")
$lines.Add("# Both take a BASE url; each appends the path its API uses (B3: /v1/responses).")
foreach ($name in $derived.Keys) {
    $lines.Add("$name=$($derived[$name])")
}

# UTF-8 **without** a BOM: PowerShell's `-Encoding utf8` writes one on 5.1, and a BOM on the
# first line turns the first variable into an unreadable name.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutFile, ($lines -join "`n") + "`n", $utf8NoBom)

Write-Host "wrote $OutFile  ($baseCount base + $($derived.Count) rendered = $($baseCount + $derived.Count) variables)" -ForegroundColor Cyan
