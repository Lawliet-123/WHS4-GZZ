param(
    [ValidateSet('offline','smoke','observe','enforce')][string]$Mode='offline',
    [string]$Python='python',
    [string]$Out,
    [switch]$Signatures,
    [string]$ProbeSys,
    [switch]$SkipDriverLoad,
    [string]$LiveEvidenceRoot,
    [switch]$FreshValidation,
    [ValidateRange(0,4096)][int]$CodeScans=520
)
$ErrorActionPreference='Stop'
if (!$Out) { $Out = Join-Path $PSScriptRoot ('runs\' + $Mode + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')) }
if ($Mode -eq 'offline') {
    if ($ProbeSys) { throw '-ProbeSys requires observe or enforce.' }
    if ($LiveEvidenceRoot) { throw '-LiveEvidenceRoot requires observe or enforce.' }
    if ($FreshValidation) { throw '-FreshValidation requires observe or enforce.' }
    & $Python (Join-Path $PSScriptRoot 'validation\offline.py') --out $Out
} else {
    if ($FreshValidation -and $LiveEvidenceRoot) { throw '-FreshValidation cannot import historical evidence.' }
    $extra=@()
    if ($Signatures) { $extra += '--signatures' }
    if ($ProbeSys) { $extra += @('--probe-sys',$ProbeSys) }
    if ($SkipDriverLoad) { $extra += '--skip-driver-load' }
    if ($LiveEvidenceRoot) { $extra += @('--live-evidence-root', (Resolve-Path -LiteralPath $LiveEvidenceRoot).Path) }
    $runner = if ($FreshValidation) { 'validation\fresh.py' } else { 'validation\live.py' }
    & $Python (Join-Path $PSScriptRoot $runner) --mode $Mode --out $Out --code-scans $CodeScans @extra
}
$result = $LASTEXITCODE
Write-Host "Reports: $Out"
if ($result -eq 2) { Write-Host 'Partial coverage: NOT_RUN / INCONCLUSIVE items remain. This is not an all-pass result.' }
exit $result
