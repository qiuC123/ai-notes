[CmdletBinding()]
param(
    [string]$InputFile = (Join-Path $PSScriptRoot '..\cross-project-impact-v1\blind-input.json'),
    [switch]$Check,
    [switch]$Run
)
$ErrorActionPreference = 'Stop'
$chemistRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$chemistPython = Join-Path $chemistRepo '.venv\Scripts\python.exe'
$chemistInput = (Resolve-Path -LiteralPath $InputFile).Path
if (-not (Test-Path -LiteralPath $chemistPython)) { throw 'Missing repository .venv Python; install Ai Notes dependencies first.' }
$chemistNames = @('PYTHONPATH', 'PYTHONIOENCODING', 'CHEMIST_PYTHON', 'CHEMIST_INPUT', 'CHEMIST_RUN_DIR', 'CHEMIST_RUN_ID', 'PI_OFFLINE')
$chemistSaved = @{}
foreach ($chemistName in $chemistNames) { $chemistSaved[$chemistName] = [Environment]::GetEnvironmentVariable($chemistName, 'Process') }
$chemistCode = 1
try {
    $env:PYTHONPATH = Join-Path $chemistRepo 'src'
    $env:PYTHONIOENCODING = 'utf-8'
    $chemistProbe = '{"action":"context","params":{}}' | & $chemistPython (Join-Path $PSScriptRoot 'worker.py') --input $chemistInput
    if ($LASTEXITCODE -ne 0) { throw "Preflight failed: $chemistProbe" }
    if ($Check) {
        Write-Output $chemistProbe
        $chemistCode = 0
    } else {
        # Resolve before changing cwd. No package install or global configuration edits.
        $chemistPi = (Get-Command pi -ErrorAction Stop).Source
        $chemistRun = 'project-chemist-' + [guid]::NewGuid().ToString()
        $chemistDir = Join-Path ([IO.Path]::GetTempPath()) $chemistRun
        New-Item -ItemType Directory -Path $chemistDir | Out-Null
        Copy-Item -LiteralPath $chemistInput -Destination (Join-Path $chemistDir 'blind-input.json')
        $env:CHEMIST_PYTHON = $chemistPython
        $env:CHEMIST_INPUT = Join-Path $chemistDir 'blind-input.json'
        $env:CHEMIST_RUN_DIR = $chemistDir
        $env:CHEMIST_RUN_ID = $chemistRun
        $env:PI_OFFLINE = '1'
        Write-Output "Run directory: $chemistDir"
        Write-Output 'This uses your configured Pi model. Authorized source snippets may be sent to that provider.'
        Push-Location -LiteralPath $chemistDir
        try {
            if ($Run) {
                $chemistCli = Join-Path (Split-Path $chemistPi) 'node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js'
                if (-not (Test-Path -LiteralPath $chemistCli)) { throw 'Headless mode requires the npm Pi installation; use interactive mode for other installations.' }
                & $chemistPython (Join-Path $PSScriptRoot 'run.py') --pi-cli $chemistCli
            } else {
                & $chemistPi --no-approve --no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files --no-builtin-tools --no-session -e (Join-Path $PSScriptRoot 'extension.ts')
            }
            $chemistCode = $LASTEXITCODE
        } finally { Pop-Location }
    }
} finally {
    foreach ($chemistName in $chemistNames) { [Environment]::SetEnvironmentVariable($chemistName, $chemistSaved[$chemistName], 'Process') }
}
exit $chemistCode
