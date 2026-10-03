param(
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [string]$ModelEnvFile,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$digestCodeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$digestPython = Join-Path $digestCodeRoot '.venv/Scripts/python.exe'
$digestArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'one_tick.py'), '--root', $StateRoot, '--max-jobs', '3')
if ($ModelEnvFile) { $digestArgs += @('--model-env-file', (Resolve-Path -LiteralPath $ModelEnvFile).Path) }
if ($Execute) { $digestArgs += '--execute' }
Push-Location -LiteralPath $digestCodeRoot
try {
    & $digestPython @digestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
