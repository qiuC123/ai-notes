[CmdletBinding()]
param([ValidateSet('Start','Stop','Status')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$mobileRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$mobileState = Join-Path $mobileRepo 'work\mobile-chemist'
$mobileScript = Join-Path $PSScriptRoot 'mobile-start.ps1'
$mobileEntry = Join-Path $PSScriptRoot 'mobile.py'
$mobileProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and
    $_.CommandLine.Contains($mobileEntry) -and $_.CommandLine -match '\sserve(?:\s|$)'
})
if ($Action -eq 'Stop') {
    foreach ($mobileProcess in $mobileProcesses) {
        # The complete executable and script identity were checked above.
        & taskkill.exe /PID $mobileProcess.ProcessId /T /F
    }
    return
}
if ($Action -eq 'Start') {
    if ($mobileProcesses.Count -gt 0) { Write-Output 'Already running'; return }
    if (-not (Test-Path -LiteralPath (Join-Path $mobileState 'feishu-credential.xml'))) {
        throw 'Run mobile-configure.ps1 first'
    }
    $mobileArguments = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $mobileScript + '"'))
    $mobileModulePath = $env:PSModulePath
    try {
        $env:PSModulePath = $null
        $mobileProcess = Start-Process powershell.exe -ArgumentList $mobileArguments -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $mobileState 'launcher-out.log') `
            -RedirectStandardError (Join-Path $mobileState 'launcher-error.log')
    } finally { $env:PSModulePath = $mobileModulePath }
    Write-Output ('Launcher PID: ' + $mobileProcess.Id + '; use Status to verify the Python process and connection.')
    return
}
$mobileProcesses | Select-Object ProcessId, ParentProcessId, CreationDate
foreach ($mobileProcess in $mobileProcesses) {
    Get-NetTCPConnection -OwningProcess $mobileProcess.ProcessId -State Established -ErrorAction SilentlyContinue |
        Select-Object OwningProcess, RemoteAddress, RemotePort, State
}
& $mobileScript -Action status
