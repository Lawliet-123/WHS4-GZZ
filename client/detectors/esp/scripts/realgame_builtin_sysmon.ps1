<#
.SYNOPSIS
Temporarily use the Windows Sysmon optional feature for the consented game test.
.DESCRIPTION
Requires elevation. Only enables a previously disabled Sysmon feature, never
reboots, accepts a EULA automatically, replaces existing Sysmon, or clears logs.
Cleanup only removes this owned installation and restores the disabled feature.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Install','Cleanup')][string]$Action,
    [Parameter(Mandatory=$true)][string]$StateDirectory,
    [string]$PythonExecutable = 'python.exe'
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\..'))
$logRoot=[IO.Path]::GetFullPath((Join-Path $repo 'logs'))
$work=[IO.Path]::GetFullPath($StateDirectory)
if (-not $work.StartsWith($logRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'state_directory_must_be_inside_repo_logs' }
$statePath=Join-Path $work 'builtin-state.json'
$binary=Join-Path $env:WINDIR 'System32\Sysmon.exe'
$state=[ordered]@{action=$Action;status='preflight';owned_feature=$false;owned_service=$false;restart_needed=$false;utc=[DateTime]::UtcNow.ToString('o')}
$mayWrite=$false
$installer=$null
function Save-State {
    $state.utc=[DateTime]::UtcNow.ToString('o')
    [IO.File]::WriteAllText($statePath,($state|ConvertTo-Json -Depth 4)+"`n",[Text.UTF8Encoding]::new($false))
}
try {
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'not_elevated' }
    if ($Action -eq 'Install' -and -not(Test-Path -LiteralPath $work)) { New-Item -ItemType Directory -Path $work > $null }
    if (-not(Test-Path -LiteralPath $work -PathType Container)) { throw 'state_directory_missing' }
    if ($Action -eq 'Install') {
        if (Test-Path -LiteralPath $statePath) { throw 'existing_state_not_overwritten' }
        if (@(Get-CimInstance Win32_Service -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'").Count) { throw 'existing_sysmon_not_modified' }
        if (@(Get-CimInstance Win32_SystemDriver -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'").Count) { throw 'existing_driver_not_modified' }
        $feature=Get-WindowsOptionalFeature -Online -FeatureName Sysmon
        if ([string]$feature.State -ne 'Disabled') { throw 'feature_not_initially_disabled' }
        $mayWrite=$true
        $state.original_feature_state='Disabled'
        # Retain ownership of the attempted feature change even if DISM fails
        # after enabling it. Cleanup can then restore the original state.
        $state.owned_feature=$true
        $state.status='enabling_feature_no_restart'
        Save-State
        $enabled=Enable-WindowsOptionalFeature -Online -FeatureName Sysmon -NoRestart
        $state.owned_feature=$true
        $state.restart_needed=[bool]$enabled.RestartNeeded
        $current=Get-WindowsOptionalFeature -Online -FeatureName Sysmon
        $state.feature_state=[string]$current.State
        if ($state.restart_needed -or [string]$current.State -ne 'Enabled') { $state.status='restart_required_not_performed';Save-State;exit 3 }
        if (-not(Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'builtin_binary_missing' }
        $sig=Get-AuthenticodeSignature -LiteralPath $binary
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '^CN=Microsoft (Windows|Windows Publisher),') { throw 'builtin_signature_invalid' }
        $state.binary_sha256=(Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash
        $state.binary_version=(Get-Item -LiteralPath $binary).VersionInfo.FileVersion
        # Read the signed binary's schema without installing or accepting a license.
        $schemaCode=@'
import json,subprocess,sys,xml.etree.ElementTree as E
p=subprocess.run([sys.argv[1],'-s'],capture_output=True,timeout=20)
b=p.stdout
t=b.decode('utf-16',errors='replace') if b[:2] in (b'\xff\xfe',b'\xfe\xff') or b'\x00' in b[:80] else b.decode('utf-8',errors='replace')
i=t.find('<manifest')
r=E.fromstring(t[i:]) if i>=0 else None
if p.returncode or r is None: sys.exit(2)
print(json.dumps({'version':r.get('schemaversion'),'rules':sorted({x.get('rulename') for x in r.iter('event') if x.get('rulename')})}))
'@
        $schemaPython=(Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop).Source
        $schemaText=& $schemaPython -B -I -c $schemaCode $binary
        if ($LASTEXITCODE -ne 0) { throw 'schema_query_failed' }
        $schema=($schemaText -join "`n")|ConvertFrom-Json
        if ($schema.version -notmatch '^4\.\d{2}$') { throw 'unexpected_schema' }
        [xml]$profile=Get-Content -LiteralPath (Join-Path $PSScriptRoot '..\sysmon-realgame-only.xml') -Raw
        foreach($rule in $profile.SelectNodes('//*[@onmatch]')) { if($rule.Name -notin $schema.rules) { throw 'rule_not_supported' } }
        $profile.Sysmon.SetAttribute('schemaversion',[string]$schema.version)
        $policy=Join-Path $work 'builtin-game-only.xml'
        $profile.Save($policy)
        $state.policy_schema=[string]$schema.version
        $state.status='installing_scoped_service'
        Save-State
        $installer=Start-Process -FilePath $binary -ArgumentList @('-i',('"'+$policy+'"')) -WindowStyle Normal -PassThru -RedirectStandardOutput (Join-Path $work 'builtin-stdout.txt') -RedirectStandardError (Join-Path $work 'builtin-stderr.txt')
        # Open a process handle before waiting so PowerShell retains ExitCode.
        $installer.Handle > $null
        if (-not $installer.WaitForExit(120000)) { throw 'installer_timeout' }
        $installer.WaitForExit()
        $state.installer_exit_code=$installer.ExitCode
        $services=@(Get-CimInstance Win32_Service -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'")
        if($services.Count -eq 1) { $state.owned_service=$true;$state.service_name=$services[0].Name }
        if($installer.ExitCode -ne 0 -or $services.Count -ne 1 -or $services[0].State -ne 'Running') { throw 'service_not_running' }
        $channel=Get-WinEvent -ListLog 'Microsoft-Windows-Sysmon/Operational'
        if (-not $channel.IsEnabled) { throw 'channel_not_enabled' }
        $state.status='installed_running'
        Save-State
    } else {
        if (-not(Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'ownership_missing' }
        $prior=Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json
        if($prior.owned_feature -ne $true -or $prior.original_feature_state -ne 'Disabled') { throw 'feature_not_owned' }
        $state=[ordered]@{}
        foreach($property in $prior.PSObject.Properties) { $state[$property.Name]=$property.Value }
        $mayWrite=$true
        $state.action='Cleanup'
        $services=@(Get-CimInstance Win32_Service -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'")
        if($services.Count) {
            if($prior.owned_service -ne $true -or $services.Count -ne 1 -or $services[0].Name -cne $prior.service_name) { throw 'service_not_owned' }
            if((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -cne $prior.binary_sha256) { throw 'binary_identity_changed' }
            $cleanup=Start-Process -FilePath $binary -ArgumentList '-u' -WindowStyle Hidden -PassThru
            $cleanup.Handle > $null
            if(-not $cleanup.WaitForExit(120000)) { throw 'cleanup_timeout' }
            $cleanup.WaitForExit()
            if($cleanup.ExitCode -ne 0) { throw 'service_cleanup_failed' }
        }
        if(@(Get-CimInstance Win32_Service -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'").Count) { throw 'service_remains' }
        if(@(Get-CimInstance Win32_SystemDriver -Filter "Name LIKE '%Sysmon%' OR DisplayName LIKE '%Sysmon%'").Count) { throw 'driver_remains' }
        $restored=Disable-WindowsOptionalFeature -Online -FeatureName Sysmon -NoRestart
        $state.restart_needed=[bool]$restored.RestartNeeded
        $state.feature_state=[string](Get-WindowsOptionalFeature -Online -FeatureName Sysmon).State
        $state.status='restored_disabled_no_log_clear'
        if($state.feature_state -ne 'Disabled') { $state.status='restore_pending_no_restart' }
        Save-State
    }
    exit 0
} catch {
    if($null -ne $installer -and -not $installer.HasExited) { $installer.Kill();$installer.WaitForExit(10000)>$null }
    $state.status='failed'
    $state.failure_type=$_.Exception.GetType().Name
    $codes=@('not_elevated','state_directory_missing','existing_state_not_overwritten','existing_sysmon_not_modified','existing_driver_not_modified','feature_not_initially_disabled','builtin_binary_missing','builtin_signature_invalid','schema_query_failed','unexpected_schema','rule_not_supported','installer_timeout','service_not_running','channel_not_enabled','ownership_missing','feature_not_owned','service_not_owned','binary_identity_changed','cleanup_timeout','service_cleanup_failed','service_remains','driver_remains')
    if($_.Exception.Message -in $codes) { $state.failure_code=$_.Exception.Message }
    if($mayWrite) { Save-State }
    exit 2
}
