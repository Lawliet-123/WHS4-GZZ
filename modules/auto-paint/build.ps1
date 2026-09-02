param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [ValidateSet("x64")]
    [string]$Platform = "x64"
)

$ErrorActionPreference = "Stop"
$moduleRoot = $PSScriptRoot
$sourceRoot = Join-Path $moduleRoot "src"
$nativeDir = Join-Path $moduleRoot "Scripts\native"
$objectDir = Join-Path $moduleRoot ".build\$Configuration"
$bridgeSource = Join-Path $sourceRoot "bridge\bridge.cpp"
$injectorSource = Join-Path $sourceRoot "injector\injector.cpp"

foreach ($source in @($bridgeSource, $injectorSource)) {
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Source not found: $source"
    }
}
New-Item -ItemType Directory -Force -Path $nativeDir, $objectDir | Out-Null

function Quote-CmdArg([string]$Value) {
    if ($Value -match '^[A-Za-z0-9_./:=+\-\\]+$') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-VsDevCmd {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) {
        return ""
    }
    $vsInstall = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vsInstall) {
        return ""
    }
    $candidate = Join-Path $vsInstall "Common7\Tools\VsDevCmd.bat"
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }
    return ""
}

function Invoke-Msvc([string[]]$Arguments) {
    if (Get-Command "cl.exe" -ErrorAction SilentlyContinue) {
        & cl.exe @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "cl.exe failed with exit code $LASTEXITCODE"
        }
        return
    }

    $vsDevCmd = Get-VsDevCmd
    if (-not $vsDevCmd) {
        throw "MSVC was not found. Install Visual Studio 2022 Build Tools with Desktop development with C++."
    }
    $argumentText = ($Arguments | ForEach-Object { Quote-CmdArg $_ }) -join " "
    $commandLine = "$(Quote-CmdArg $vsDevCmd) -arch=x64 -host_arch=x64 >nul && cl.exe $argumentText"
    cmd.exe /d /c $commandLine
    if ($LASTEXITCODE -ne 0) {
        throw "cl.exe failed with exit code $LASTEXITCODE"
    }
}

$common = @("/nologo", "/std:c++17", "/EHsc", "/utf-8", "/DUNICODE", "/D_UNICODE")
if ($Configuration -eq "Debug") {
    $compileMode = @("/Od", "/Zi")
} else {
    $compileMode = @("/O2")
}

$bridge = Join-Path $nativeDir "runtime-bridge.dll"
$injector = Join-Path $nativeDir "runtime-injector.exe"
$bridgeObject = Join-Path $objectDir "bridge.obj"
$injectorObject = Join-Path $objectDir "injector.obj"
$bridgeImportLibrary = Join-Path $objectDir "runtime-bridge.lib"
$bridgePdb = Join-Path $objectDir "runtime-bridge.pdb"
$injectorPdb = Join-Path $objectDir "runtime-injector.pdb"

Push-Location $moduleRoot
try {
    Invoke-Msvc ($common + $compileMode + @(
        "/LD",
        $bridgeSource,
        "/Fo:$bridgeObject",
        "/Fe:$bridge",
        "Ws2_32.lib",
        "Gdi32.lib",
        "User32.lib",
        "/link",
        "/IMPLIB:$bridgeImportLibrary",
        "/PDB:$bridgePdb"
    ))
    Invoke-Msvc ($common + $compileMode + @(
        $injectorSource,
        "/Fo:$injectorObject",
        "/Fe:$injector",
        "Bcrypt.lib",
        "/link",
        "/PDB:$injectorPdb"
    ))
}
finally {
    Pop-Location
}

foreach ($artifact in @($bridge, $injector)) {
    if (-not (Test-Path -LiteralPath $artifact)) {
        throw "Build artifact not found: $artifact"
    }
}

& $injector --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Injector ABI self-test failed with exit code $LASTEXITCODE"
}

Write-Host "Built Auto Paint native files:"
Write-Host "  $bridge"
Write-Host "  $injector"
