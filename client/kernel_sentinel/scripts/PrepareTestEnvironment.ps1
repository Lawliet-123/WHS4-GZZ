param(
    [Parameter(Mandatory=$true)][string]$SysPath,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256,
    [Parameter(Mandatory=$true)][string]$CertificatePath,
    [Parameter(Mandatory=$true)][string]$CertificateThumbprint,
    [Parameter(Mandatory=$true)][ValidateSet('true','false')][string]$EffectiveTestSigning
)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if (!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator privileges are required for test-environment preparation.'
}
$source=(Resolve-Path -LiteralPath $SysPath).Path
if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $ExpectedSha256) {
    throw 'Bundled SYS hash mismatch; no boot or trust settings changed.'
}
$certPath=(Resolve-Path -LiteralPath $CertificatePath).Path
$cert=New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath)
if ($cert.Thumbprint -ne $CertificateThumbprint) { throw 'Pinned test certificate mismatch.' }
$sig=Get-AuthenticodeSignature -LiteralPath $source
if (!$sig.SignerCertificate -or $sig.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
    throw 'The SYS does not carry the pinned test signer.'
}
$needsReboot=$EffectiveTestSigning -eq 'false'
if ($needsReboot) {
    & bcdedit.exe /set '{current}' testsigning on
    if ($LASTEXITCODE -ne 0) {
        throw 'TESTSIGNING change rejected. Secure Boot/BitLocker/Defender settings were not changed.'
    }
}
foreach ($store in @('Root','TrustedPublisher')) {
    if (!(Test-Path "Cert:\LocalMachine\$store\$CertificateThumbprint")) {
        & certutil.exe -addstore $store $certPath
        if ($LASTEXITCODE -ne 0) { throw "Test certificate import failed in $store." }
        Write-Host "Imported pinned test certificate into $store."
    } else {
        Write-Host "Pinned certificate already exists in $store."
    }
}
if ($needsReboot) {
    Write-Host 'TEST_SIGNING_REBOOT_REQUIRED: save your work, restart Windows, then launch again.'
    exit 3010
}
Write-Host 'Test signing is already effective; pinned certificate trust is prepared.'
