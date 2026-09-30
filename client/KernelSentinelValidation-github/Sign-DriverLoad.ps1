param(
    [Parameter(Mandatory=$true)][string]$Thumbprint,
    [ValidateSet('CurrentUser','LocalMachine')][string]$StoreLocation='CurrentUser',
    [string]$SignTool,
    [string]$ProbeSys
)
$ErrorActionPreference='Stop'
if (!$ProbeSys) {
    $ProbeSys=Join-Path $PSScriptRoot 'bin\driver\KsValidationProbe.sys'
    if (!(Test-Path -LiteralPath $ProbeSys -PathType Leaf)) {
        $ProbeSys=Join-Path $PSScriptRoot 'driver-load-fixture\KsValidationProbe.sys'
    }
}
$Thumbprint=($Thumbprint -replace '\s','')
if ($Thumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'Specify the 40-digit thumbprint of your existing VM-trusted code-signing certificate.' }
$cert=Get-Item -LiteralPath "Cert:\$StoreLocation\My\$Thumbprint"
if (!$cert.HasPrivateKey) { throw 'The selected certificate has no private key on this computer.' }
if (!(Test-Path -LiteralPath $ProbeSys -PathType Leaf)) { throw "Missing fixture: $ProbeSys" }
if (!$SignTool) {
    $kitBin=Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $SignTool=Get-ChildItem -Path (Join-Path $kitBin '*\x64\signtool.exe') -File |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (!$SignTool -or !(Test-Path -LiteralPath $SignTool -PathType Leaf)) { throw 'Windows SDK signtool.exe is required; specify -SignTool.' }
$signArgs=@('sign','/fd','SHA256','/s','My','/sha1',$Thumbprint)
if ($StoreLocation -eq 'LocalMachine') { $signArgs += '/sm' }
$signArgs += (Resolve-Path -LiteralPath $ProbeSys).Path
& $SignTool @signArgs
if ($LASTEXITCODE -ne 0) { throw "Signing failed: $LASTEXITCODE" }
Write-Host "Signed fixture: $ProbeSys"
Write-Host 'Certificate trust and boot settings were not changed.'
