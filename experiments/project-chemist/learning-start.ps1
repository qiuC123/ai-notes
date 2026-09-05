[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$QueueFile,
    [Parameter(Mandatory)][string]$LearningRoot,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$chemistRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$chemistPython = Join-Path $chemistRepo '.venv\Scripts\python.exe'
$chemistQueue = (Resolve-Path -LiteralPath $QueueFile).Path
$chemistRoot = (Resolve-Path -LiteralPath $LearningRoot).Path
$chemistNames = @('PYTHONPATH','PYTHONIOENCODING','CHEMIST_PYTHON','CHEMIST_INPUT','CHEMIST_LEARNING_ROOT','CHEMIST_RUN_DIR','CHEMIST_RUN_ID','PI_OFFLINE')
$chemistSaved = @{}
foreach($name in $chemistNames){$chemistSaved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$chemistCode=1
try {
    $env:PYTHONPATH=Join-Path $chemistRepo 'src'
    $env:PYTHONIOENCODING='utf-8'
    $probe='{"action":"context","params":{}}' | & $chemistPython (Join-Path $PSScriptRoot 'learning_worker.py') --input $chemistQueue --root $chemistRoot
    if($LASTEXITCODE -ne 0){throw "Learning preflight failed: $probe"}
    if($Check){Write-Output $probe; $chemistCode=0}
    else {
        $chemistPi=(Get-Command pi -ErrorAction Stop).Source
        $chemistCli=Join-Path (Split-Path $chemistPi) 'node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js'
        if(-not (Test-Path -LiteralPath $chemistCli)){throw 'Expected npm Pi installation'}
        $env:CHEMIST_RUN_ID='project-learning-'+[guid]::NewGuid().ToString()
        $env:CHEMIST_RUN_DIR=Join-Path $chemistRoot ('outputs\pi\'+$env:CHEMIST_RUN_ID)
        New-Item -ItemType Directory -Path $env:CHEMIST_RUN_DIR | Out-Null
        $env:CHEMIST_PYTHON=$chemistPython
        $env:CHEMIST_INPUT=$chemistQueue
        $env:CHEMIST_LEARNING_ROOT=$chemistRoot
        $env:PI_OFFLINE='1'
        Write-Output "Run directory: $env:CHEMIST_RUN_DIR"
        & $chemistPython (Join-Path $PSScriptRoot 'run.py') --pi-cli $chemistCli --profile learning
        $chemistCode=$LASTEXITCODE
    }
} finally {foreach($name in $chemistNames){[Environment]::SetEnvironmentVariable($name,$chemistSaved[$name],'Process')}}
exit $chemistCode
