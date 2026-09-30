param([string]$Root = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'VM administrator PowerShell is required.'
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$sysPath = Join-Path $rootPath 'KsOutsideThreadProbe.sys'
if (!(Test-Path -LiteralPath $sysPath -PathType Leaf)) { throw "Fixture missing: $sysPath" }
$subject = 'CN=KernelSentinel Validation VM Test Only'
$certificate = Get-ChildItem Cert:\LocalMachine\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date).AddDays(7) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if (!$certificate) { throw 'The dedicated KernelSentinel Validation VM Test Only signing certificate is not available in LocalMachine\My.' }
foreach ($store in @('Root', 'TrustedPublisher')) {
    if (!(Test-Path -LiteralPath "Cert:\LocalMachine\$store\$($certificate.Thumbprint)")) {
        throw "Signing certificate is not trusted in LocalMachine\$store; inspect the VM test-certificate setup."
    }
}
$evidence = Join-Path $rootPath ('runs\signing_outside_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$backup = Join-Path $evidence 'KsOutsideThreadProbe.before-signing.sys'
Copy-Item -LiteralPath $sysPath -Destination $backup
$result = Set-AuthenticodeSignature -LiteralPath $sysPath -Certificate $certificate -HashAlgorithm SHA256
$verified = Get-AuthenticodeSignature -LiteralPath $sysPath
[ordered]@{
    fixture = $sysPath
    backup = $backup
    certificateThumbprint = $certificate.Thumbprint
    signStatus = [string]$result.Status
    verificationStatus = [string]$verified.Status
    statusMessage = $verified.StatusMessage
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'signing.json') -Encoding UTF8
if ($verified.Status -ne 'Valid' -or !$verified.SignerCertificate -or
    $verified.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
    throw "Fixture signature verification failed: $($verified.Status); see $evidence"
}
Write-Host "Fixture signature: Valid; certificate: $($certificate.Thumbprint)"
Write-Host "Signing record: $evidence"
