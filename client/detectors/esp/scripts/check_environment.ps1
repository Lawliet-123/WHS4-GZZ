$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue
$service = Get-Service -Name 'Sysmon','Sysmon64' -ErrorAction SilentlyContinue |
    Select-Object -First 1
$log = Get-WinEvent -ListLog 'Microsoft-Windows-Sysmon/Operational' `
    -ErrorAction SilentlyContinue

[pscustomobject]@{
    ProjectRoot  = $projectRoot
    Python       = if ($python) { $python.Source } else { 'NOT FOUND' }
    Sysmon       = if ($service) { "$($service.Name): $($service.Status)" } else { 'NOT INSTALLED' }
    SysmonLog    = if ($log) { "Enabled=$($log.IsEnabled), Records=$($log.RecordCount)" } else { 'NOT FOUND' }
    IsAdmin      = ([Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
