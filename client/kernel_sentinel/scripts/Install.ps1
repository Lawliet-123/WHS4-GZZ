# Run in an elevated PowerShell inside the existing test VM.
param([Parameter(Mandatory=$true)][string]$SysPath)
$ErrorActionPreference='Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run PowerShell as administrator.'
}
$source = (Resolve-Path -LiteralPath $SysPath).Path
if ([IO.Path]::GetExtension($source) -ne '.sys') { throw 'Expected a .sys file.' }
$existing = Get-CimInstance -ClassName Win32_SystemDriver -Filter "Name='KernelSentinel'"
if ($existing -and $existing.State -ne 'Stopped') { throw 'Stop the collector, then sc.exe stop KernelSentinel before updating.' }
$destination = Join-Path $env:SystemRoot 'System32\drivers\KernelSentinel.sys'
if ($source -ne $destination) { Copy-Item -LiteralPath $source -Destination $destination -Force }
$binPath = '"' + $destination + '"'
if ($existing) {
    & sc.exe config KernelSentinel type= kernel start= demand binPath= $binPath
} else {
    & sc.exe create KernelSentinel type= kernel start= demand binPath= $binPath
}
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
& sc.exe start KernelSentinel
if ($LASTEXITCODE -ne 0) { throw 'Driver load failed; see README error guidance. Signing/security settings were not changed.' }
& sc.exe query KernelSentinel
