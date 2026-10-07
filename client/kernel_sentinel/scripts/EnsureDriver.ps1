param(
    [Parameter(Mandatory=$true)][string]$SysPath,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator privileges are required.'
}
if ($ExpectedSha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'ExpectedSha256 must be a pinned SHA-256.' }
$source = (Resolve-Path -LiteralPath $SysPath).Path
if ([IO.Path]::GetExtension($source) -ne '.sys') { throw 'Expected a .sys file.' }
function Test-ApprovedDriver([string]$path) {
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($hash -ne $ExpectedSha256) { throw 'Driver SHA-256 does not match the approved build.' }
    $sig = Get-AuthenticodeSignature -LiteralPath $path
    if ($sig.Status -ne 'Valid') { throw "Driver signature validation failed: $($sig.Status)" }
}
Test-ApprovedDriver $source
$existing = Get-CimInstance Win32_SystemDriver -Filter "Name='KernelSentinel'"
$destination = Join-Path $env:SystemRoot 'System32\drivers\KernelSentinel.sys'
if ($existing -and $existing.State -ne 'Stopped') {
    # Never stop/replace a driver possibly held by another collector.
    $actual = $existing.PathName.Trim('"')
    if ($actual.StartsWith('\??\')) { $actual = $actual.Substring(4) }
    if ($actual.StartsWith('\SystemRoot\')) { $actual = Join-Path $env:SystemRoot $actual.Substring(12) }
    $actual = [Environment]::ExpandEnvironmentVariables($actual)
    Test-ApprovedDriver $actual
    Write-Host 'KernelSentinel already running with the approved driver.'
    exit 0
}
if ($source -ne $destination) { Copy-Item -LiteralPath $source -Destination $destination -Force }
# Recheck copied bytes and trust before the service is changed or started.
Test-ApprovedDriver $destination
$binPath = '"' + $destination + '"'
if ($existing) {
    & sc.exe config KernelSentinel type= kernel start= demand binPath= $binPath
} else {
    & sc.exe create KernelSentinel type= kernel start= demand binPath= $binPath
}
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
& sc.exe start KernelSentinel
if ($LASTEXITCODE -ne 0) { throw 'Driver load failed. Signing/security settings were not changed.' }
$loaded = Get-CimInstance Win32_SystemDriver -Filter "Name='KernelSentinel'"
if ($loaded.State -ne 'Running') { throw 'Driver did not reach Running state.' }
Write-Host 'KernelSentinel driver installation and start completed.'
