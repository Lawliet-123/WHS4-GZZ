param(
    [string]$Root = (Get-Location).Path,
    [switch]$EnableTestSigning
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in administrator Windows PowerShell inside the test VM.'
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$sysPath = Join-Path $rootPath 'bin\driver\KsValidationProbe.sys'
if (!(Test-Path -LiteralPath $sysPath -PathType Leaf)) {
    $sysPath = Join-Path $rootPath 'driver-load-fixture\KsValidationProbe.sys'
}
if (!(Test-Path -LiteralPath $sysPath -PathType Leaf)) { throw "Fixture not found: $sysPath" }
Write-Host "Signing fixture: $sysPath"
Write-Host 'This creates/trusts a dedicated test code-signing certificate in THIS VM.'

$subject = 'CN=KernelSentinel Validation VM Test Only'
$cert = Get-ChildItem Cert:\LocalMachine\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey -and $_.NotBefore -le (Get-Date) -and $_.NotAfter -gt (Get-Date).AddDays(7) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if (!$cert) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $subject `
        -FriendlyName 'KernelSentinel Validation VM Test Only' `
        -CertStoreLocation 'Cert:\LocalMachine\My' -KeyAlgorithm RSA -KeyLength 2048 `
        -HashAlgorithm SHA256 -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddMonths(6)
}
$evidence = Join-Path $rootPath ('runs\signing_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$cerPath = Join-Path $evidence 'KernelSentinelValidationTest.cer'
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null
$addedStores = @()
foreach ($store in @('Root','TrustedPublisher')) {
    if (!(Test-Path -LiteralPath "Cert:\LocalMachine\$store\$($cert.Thumbprint)")) {
        Import-Certificate -FilePath $cerPath -CertStoreLocation "Cert:\LocalMachine\$store" | Out-Null
        $addedStores += $store
    }
}
$backup = Join-Path $evidence 'KsValidationProbe.before-signing.sys'
Copy-Item -LiteralPath $sysPath -Destination $backup
$result = Set-AuthenticodeSignature -LiteralPath $sysPath -Certificate $cert -HashAlgorithm SHA256
$verified = Get-AuthenticodeSignature -LiteralPath $sysPath
[ordered]@{
    fixture = $sysPath
    backup = $backup
    certificateThumbprint = $cert.Thumbprint
    certificateStore = 'LocalMachine\My'
    addedTrustStores = $addedStores
    signStatus = [string]$result.Status
    verificationStatus = [string]$verified.Status
    statusMessage = $verified.StatusMessage
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'signing.json') -Encoding UTF8
if ($verified.Status -ne 'Valid' -or !$verified.SignerCertificate -or $verified.SignerCertificate.Thumbprint -ne $cert.Thumbprint) {
    throw "Embedded signature verification failed: $($verified.Status); $($verified.StatusMessage). Details: $evidence"
}
Write-Host "Embedded signature: Valid; certificate: $($cert.Thumbprint)"
Write-Host "Backup and signing record: $evidence"
Write-Host 'Authenticode validity alone does not prove the kernel will load the driver.'

if ($EnableTestSigning) {
    & "$env:SystemRoot\System32\bcdedit.exe" /set '{current}' testsigning on
    if ($LASTEXITCODE -ne 0) {
        throw 'TESTSIGNING could not be enabled. Preserve the error above; do not continue to the load test yet.'
    }
    Write-Host 'TESTSIGNING configured. Restart the test VM manually, then start KernelSentinel and rerun Run.ps1.'
} else {
    Write-Host 'Boot settings unchanged. To configure test signing explicitly, rerun with -EnableTestSigning, then restart the VM.'
}
