[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SysmonPath,

    [switch]$UpdateExisting
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$adminRole = [Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $principal.IsInRole($adminRole)) {
    throw 'Run this script from an Administrator PowerShell window.'
}

$resolvedSysmon = (Resolve-Path -LiteralPath $SysmonPath).Path
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = Join-Path $projectRoot 'sysmon-config.xml'

$service = Get-Service -Name 'Sysmon','Sysmon64' -ErrorAction SilentlyContinue |
    Select-Object -First 1

if ($null -eq $service) {
    & $resolvedSysmon -accepteula -i $configPath
} else {
    if (-not $UpdateExisting) {
        throw ('Sysmon is already installed. Applying this project configuration ' +
            'replaces its active filtering configuration. Re-run with ' +
            '-UpdateExisting only after reviewing sysmon-config.xml.')
    }
    & $resolvedSysmon -c $configPath
}

if ($LASTEXITCODE -ne 0) {
    throw "Sysmon returned exit code $LASTEXITCODE."
}

Get-Service -Name 'Sysmon','Sysmon64' -ErrorAction SilentlyContinue |
    Select-Object Name, Status, StartType
