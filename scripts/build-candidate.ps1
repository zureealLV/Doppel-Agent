param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Existing project .venv is required; this build does not install dependencies.'
}
& $python (Join-Path $PSScriptRoot 'build_candidate.py') --output $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw 'Candidate build failed. Preserve its logs and manifest.' }
