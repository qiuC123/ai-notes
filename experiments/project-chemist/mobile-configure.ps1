[CmdletBinding()]
param([string]$StateDirectory)
$ErrorActionPreference = 'Stop'
if (-not $StateDirectory) { $StateDirectory = Join-Path $PSScriptRoot '..\..\work\mobile-chemist' }
$mobileRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$mobilePython = Join-Path $mobileRepo '.venv\Scripts\python.exe'
$mobileSavedPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $mobileRepo 'src'
    & $mobilePython (Join-Path $PSScriptRoot 'mobile.py') init --state $StateDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' }
    $mobileState = (Resolve-Path -LiteralPath $StateDirectory).Path
    $mobileAppId = Read-Host 'Independent Feishu bot App ID'
    if ($mobileAppId -notmatch '^cli_[a-zA-Z0-9]+$') { throw 'Invalid App ID' }
    $mobileSecret = Read-Host 'App Secret (hidden; saved with Windows DPAPI)' -AsSecureString
    if ($mobileSecret.Length -eq 0) { throw 'App Secret is required' }
    $mobileCredential = [System.Management.Automation.PSCredential]::new($mobileAppId, $mobileSecret)
    $mobileCredential | Export-Clixml -LiteralPath (Join-Path $mobileState 'feishu-credential.xml')
    $mobileConfig = Get-Content -Raw -LiteralPath (Join-Path $mobileState 'config.json') | ConvertFrom-Json
    Write-Host 'Saved. Only this Windows user on this computer can decrypt the secret.'
    Write-Host ('Pair in a private chat with the bot: 配对 ' + $mobileConfig.pairing_code)
} finally {
    $env:PYTHONPATH = $mobileSavedPath
}
