param(
    [Parameter(Mandatory = $true)][string]$StateRoot,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$digestCodeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$digestPython = Join-Path $digestCodeRoot '.venv/Scripts/python.exe'
$digestArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'one_tick.py'), '--root', $StateRoot, '--max-jobs', '3')
if ($Execute) { $digestArgs += '--execute' }
Push-Location -LiteralPath $digestCodeRoot
try {
    & $digestPython @digestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
