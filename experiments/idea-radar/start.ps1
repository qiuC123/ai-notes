[CmdletBinding()]
param([ValidateSet('init','doctor','status','serve','check-app','register')][string]$Action = 'status', [string]$AppId)
$ErrorActionPreference = 'Stop'
$radarRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$radarState = Join-Path $radarRepo 'work\idea-radar'
$radarPython = Join-Path $radarRepo '.venv\Scripts\python.exe'
$radarNames = @('PYTHONPATH','PYTHONIOENCODING','CHEMIST_FEISHU_APP_ID','CHEMIST_FEISHU_APP_SECRET')
$radarSaved = @{}
foreach ($name in $radarNames) { $radarSaved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PYTHONPATH = Join-Path $radarRepo 'src'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:CHEMIST_FEISHU_APP_ID = $null
    $env:CHEMIST_FEISHU_APP_SECRET = $null
    if ($Action -eq 'register') {
        & $radarPython (Join-Path $PSScriptRoot 'radar.py') init
        if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' }
        $radarRegister = Join-Path $PSScriptRoot '..\project-chemist\mobile-register.py'
        $radarArgs = @('--state', $radarState, '--name', '开发方向雷达', '--description', '寻找海外应用、SaaS 和小游戏方向，核对需求线索并生成验证建议')
        if ($AppId) { $radarArgs += @('--app-id', $AppId) }
        & $radarPython $radarRegister @radarArgs
    } else {
        $radarCredentialPath = Join-Path $radarState 'feishu-credential.xml'
        if (Test-Path -LiteralPath $radarCredentialPath) {
            $radarCredential = Import-Clixml -LiteralPath $radarCredentialPath
            $env:CHEMIST_FEISHU_APP_ID = $radarCredential.UserName
            $env:CHEMIST_FEISHU_APP_SECRET = $radarCredential.GetNetworkCredential().Password
        }
        & $radarPython (Join-Path $PSScriptRoot 'radar.py') $Action
    }
    $radarExit = $LASTEXITCODE
} finally {
    foreach ($name in $radarNames) { [Environment]::SetEnvironmentVariable($name, $radarSaved[$name], 'Process') }
}
exit $radarExit
