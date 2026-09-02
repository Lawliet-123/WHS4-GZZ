param(
    [string]$RuntimeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) {
    $OutDir = Join-Path $RuntimeRoot ".build\bin"
}
$ObjDir = Join-Path $RuntimeRoot ".build\obj"
$PackageNative = Join-Path (Split-Path $RuntimeRoot -Parent) "app\native"
New-Item -ItemType Directory -Force -Path $OutDir, $ObjDir, $PackageNative | Out-Null

$BridgeSource = Join-Path $RuntimeRoot "src\bridge.cpp"
$InjectorSource = Join-Path $RuntimeRoot "src\injector.cpp"

function Quote-CmdArg([string]$Value) {
    if ($Value -match '^[A-Za-z0-9_./:=+\-\\]+$') { return $Value }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-VsDevCmd {
    $VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $VsWhere)) { return "" }
    $VsInstall = & $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $VsInstall) { return "" }
    $VsDevCmd = Join-Path $VsInstall "Common7\Tools\VsDevCmd.bat"
    if (Test-Path -LiteralPath $VsDevCmd) { return $VsDevCmd }
    return ""
}

function Invoke-VsToolCommand {
    param(
        [Parameter(Mandatory = $true)][string]$ToolName,
        [Parameter(Mandatory = $true)][string[]]$ToolArgs
    )
    if (Get-Command $ToolName -ErrorAction SilentlyContinue) {
        & $ToolName @ToolArgs
        if ($LASTEXITCODE -ne 0) { throw "$ToolName failed with exit code $LASTEXITCODE" }
        return
    }
    $VsDevCmd = Get-VsDevCmd
    if (-not $VsDevCmd) { throw "$ToolName or Visual Studio Build Tools was not found" }
    $ArgText = ($ToolArgs | ForEach-Object { Quote-CmdArg $_ }) -join " "
    $CommandLine = "$(Quote-CmdArg $VsDevCmd) -arch=x64 -host_arch=x64 >nul && $ToolName $ArgText"
    cmd /d /c $CommandLine
    if ($LASTEXITCODE -ne 0) { throw "$ToolName failed with exit code $LASTEXITCODE" }
}

$BridgeOutput = Join-Path $OutDir "runtime-bridge.dll"
$InjectorOutput = Join-Path $OutDir "runtime-injector.exe"

Push-Location $RuntimeRoot
try {
    Invoke-VsToolCommand -ToolName "cl.exe" -ToolArgs @(
        "/nologo", "/std:c++17", "/EHsc", "/O2", "/Gy", "/LD", $BridgeSource,
        "/Fo:$(Join-Path $ObjDir 'bridge.obj')", "/Fe:$BridgeOutput",
        "Ws2_32.lib", "User32.lib", "/link", "/OPT:REF", "/OPT:ICF"
    )
    Invoke-VsToolCommand -ToolName "cl.exe" -ToolArgs @(
        "/nologo", "/EHsc", "/O2", "/Gy", $InjectorSource,
        "/Fo:$(Join-Path $ObjDir 'injector.obj')", "/Fe:$InjectorOutput",
        "/link", "/OPT:REF", "/OPT:ICF"
    )
}
finally {
    Pop-Location
}

Copy-Item -LiteralPath $BridgeOutput -Destination (Join-Path $PackageNative "runtime-bridge.dll") -Force
Copy-Item -LiteralPath $InjectorOutput -Destination (Join-Path $PackageNative "runtime-injector.exe") -Force

Write-Host "Built original camouflage runtime for LiteV2:"
Write-Host "  $BridgeOutput"
Write-Host "  $InjectorOutput"
Write-Host "Copied package natives to: $PackageNative"
