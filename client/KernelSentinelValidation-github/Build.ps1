param([switch]$Driver, [switch]$KernelSentinel, [switch]$Unsigned, [switch]$CompileOnly, [string]$SdkVersion, [string]$Python='python')
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (!(Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio C++ build tools are required.' }
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (!$installation) { throw 'Visual Studio x64 C++ tools were not found.' }
$env:KS_VALIDATION_VSDEV = Join-Path $installation 'Common7\Tools\VsDevCmd.bat'
try {
    & $env:ComSpec /d /c (Join-Path $PSScriptRoot 'validation\build-native.cmd')
    if ($LASTEXITCODE -ne 0) { throw "Native build failed: $LASTEXITCODE" }
} finally {
    Remove-Item Env:\KS_VALIDATION_VSDEV -ErrorAction SilentlyContinue
}
if ($Driver -or $KernelSentinel) {
    $msbuild = & $vswhere -latest -products '*' -find 'MSBuild\**\Bin\amd64\MSBuild.exe' | Select-Object -First 1
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10'
    if (!$SdkVersion) {
        $SdkVersion = Get-ChildItem (Join-Path $kits 'Include') -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName 'km\ntddk.h') } |
            Where-Object { Test-Path (Join-Path $kits "Lib\$($_.Name)\km\x64\Wdmsec.lib") } |
            Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1 -ExpandProperty Name
    }
    if (!$msbuild -or !$SdkVersion) { throw 'Matching WDK headers, libraries and MSBuild are required.' }
    $extra = @()
    if ($Unsigned) { $extra += '--unsigned' }
    if ($CompileOnly) { $extra += '--compile-only' }
    if ($Driver) {
        & $Python (Join-Path $PSScriptRoot 'validation\build_driver.py') --msbuild $msbuild --sdk $SdkVersion @extra
        if ($LASTEXITCODE -ne 0) { throw "Fixture driver build failed: $LASTEXITCODE" }
    }
    if ($KernelSentinel) {
        & $Python (Join-Path $PSScriptRoot 'validation\build_driver.py') --msbuild $msbuild --sdk $SdkVersion --sentinel @extra
        if ($LASTEXITCODE -ne 0) { throw "KernelSentinel build failed: $LASTEXITCODE" }
    }
}
