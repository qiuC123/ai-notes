[CmdletBinding()]
param([ValidateSet('Start','Stop','Status')][string]$Action = 'Status')
$ErrorActionPreference = 'Stop'
$radarRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$radarState = Join-Path $radarRepo 'work\idea-radar'
$radarEntry = Join-Path $PSScriptRoot 'radar.py'
$radarStart = Join-Path $PSScriptRoot 'start.ps1'
$radarProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and
    $_.CommandLine.Contains($radarEntry) -and $_.CommandLine -match '\sserve(?:\s|$)'
})
if ($Action -eq 'Stop') {
    foreach ($radarProcess in $radarProcesses) { & taskkill.exe /PID $radarProcess.ProcessId /T /F }
    return
}
if ($Action -eq 'Start') {
    if ($radarProcesses.Count) { Write-Output 'Already running'; return }
    if (-not (Test-Path -LiteralPath (Join-Path $radarState 'feishu-credential.xml'))) { throw 'Run start.ps1 -Action register first' }
    $radarArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $radarStart + '"'),'-Action','serve')
    $radarProcess = Start-Process pwsh.exe -ArgumentList $radarArgs -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $radarState 'launcher-out.log') `
        -RedirectStandardError (Join-Path $radarState 'launcher-error.log')
    Write-Output ('Launcher PID: ' + $radarProcess.Id + '; run Status to verify.')
    return
}
$radarProcesses | Select-Object ProcessId, ParentProcessId, CreationDate | Format-Table -AutoSize
foreach ($radarProcess in $radarProcesses) {
    Get-NetTCPConnection -OwningProcess $radarProcess.ProcessId -State Established -ErrorAction SilentlyContinue |
        Select-Object OwningProcess, RemoteAddress, RemotePort, State | Format-Table -AutoSize
}
& $radarStart -Action status
