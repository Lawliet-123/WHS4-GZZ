$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argument = '-NoExit -NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $argument
    exit 0
}
$bridge = Join-Path $PSScriptRoot 'bin\KsDbgBridge.exe'
if (!(Test-Path -LiteralPath $bridge -PathType Leaf)) { throw "Bridge executable missing: $bridge" }
$debuggerDir = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\Debuggers\x64'
if (!(Test-Path -LiteralPath (Join-Path $debuggerDir 'DbgEng.dll') -PathType Leaf)) {
    throw 'Windows Debugging Tools x64 installation was not found.'
}
$env:Path = $debuggerDir + ';' + $env:Path
if (Get-Process WinDbg,kd,cdb -ErrorAction SilentlyContinue) {
    throw 'Close the current host WinDbg/KD/CDB session before starting this dedicated debugger bridge.'
}
$adapter = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias '*VMnet8*' -ErrorAction Stop |
    Where-Object { $_.IPAddress -match '^192\.168\.' } | Select-Object -First 1
if (!$adapter) { throw 'VMware VMnet8 IPv4 interface was not found.' }
$ip = $adapter.IPAddress
$key = Read-Host 'Paste the VM KDNET key once (from the VM kdnet.exe output)'
if ($key -notmatch '^[a-z0-9]+(\.[a-z0-9]+)+$') { throw 'KDNET key format invalid.' }
$random = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($random) } finally { $rng.Dispose() }
$token = [BitConverter]::ToString($random).Replace('-', '').ToLowerInvariant()
$configPath = Join-Path $PSScriptRoot 'bridge.json'
[ordered]@{host=$ip; port=50001; token=$token} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath $configPath -Encoding UTF8
$ruleName = 'KernelSentinelValidation-VMnet8-Bridge'
$newRule = $false
if (!(Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    $network = $ip.Substring(0, $ip.LastIndexOf('.')) + '.0/24'
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalAddress $ip -LocalPort 50001 -RemoteAddress $network -Program $bridge | Out-Null
    $newRule = $true
}
Write-Host "Copy $configPath to the root of the VM KernelSentinelValidation folder."
Write-Host 'Keep this window open during FreshValidation. Do not open another kernel debugger.'
try {
    & $bridge $ip 50001 $key $token
    if ($LASTEXITCODE -ne 0) { throw "Bridge exited with code $LASTEXITCODE" }
} finally {
    if ($newRule) { Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
}
