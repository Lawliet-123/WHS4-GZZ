param([string]$SdkVersion, [ValidateSet('Debug','Release')][string]$Configuration='Debug')
$ErrorActionPreference='Stop'
$root = Split-Path $PSScriptRoot -Parent
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (!(Test-Path $vswhere)) { throw 'vswhere.exe not found; install Visual Studio C++ and WDK integration.' }
$msbuild = & $vswhere -latest -products '*' -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (!$msbuild) { throw 'MSBuild not found.' }
$kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
if (!$SdkVersion) {
    $versions = Get-ChildItem (Join-Path $kits 'Include') -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'km\ntddk.h') } |
        Where-Object { Test-Path (Join-Path $kits "Lib\$($_.Name)\km\x64\Aux_Klib.lib") } |
        Where-Object { Test-Path (Join-Path $_.FullName 'ucrt\stdio.h') } |
        Sort-Object { [version]$_.Name } -Descending
    $SdkVersion = $versions | Select-Object -First 1 -ExpandProperty Name
}
if (!$SdkVersion) { throw 'Matching SDK/WDK headers and libraries not found.' }
Write-Host "Building x64 with SDK/WDK $SdkVersion"
& $msbuild (Join-Path $root 'driver\KernelSentinel.vcxproj') /m /t:Build "/p:Configuration=$Configuration" /p:Platform=x64 "/p:WindowsTargetPlatformVersion=$SdkVersion" /v:minimal
if ($LASTEXITCODE -ne 0) { throw "Build failed: $LASTEXITCODE" }
Get-ChildItem (Join-Path $root "build\x64\$Configuration") -Filter KernelSentinel.sys -Recurse |
    Select-Object FullName,Length,LastWriteTime
