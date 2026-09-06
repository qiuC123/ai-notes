[CmdletBinding()]
param(
    [ValidateSet('serve','doctor','status','check-app','init')][string]$Action = 'serve',
    [string]$StateDirectory
)
$ErrorActionPreference = 'Stop'
if (-not $StateDirectory) { $StateDirectory = Join-Path $PSScriptRoot '..\..\work\mobile-chemist' }
$mobileRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$mobileNames = @('PYTHONPATH','PYTHONIOENCODING','CHEMIST_FEISHU_APP_ID','CHEMIST_FEISHU_APP_SECRET')
$mobileSaved = @{}
foreach ($name in $mobileNames) { $mobileSaved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$mobileCode = 1
try {
    $env:PYTHONPATH = Join-Path $mobileRepo 'src'
    $env:PYTHONIOENCODING = 'utf-8'
    $mobileCredentialPath = Join-Path $StateDirectory 'feishu-credential.xml'
    if (Test-Path -LiteralPath $mobileCredentialPath) {
        $mobileCredential = Import-Clixml -LiteralPath $mobileCredentialPath
        $env:CHEMIST_FEISHU_APP_ID = $mobileCredential.UserName
        $env:CHEMIST_FEISHU_APP_SECRET = $mobileCredential.GetNetworkCredential().Password
    }
    & (Join-Path $mobileRepo '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'mobile.py') $Action --state $StateDirectory
    $mobileCode = $LASTEXITCODE
} finally {
    foreach ($name in $mobileNames) { [Environment]::SetEnvironmentVariable($name, $mobileSaved[$name], 'Process') }
}
exit $mobileCode
