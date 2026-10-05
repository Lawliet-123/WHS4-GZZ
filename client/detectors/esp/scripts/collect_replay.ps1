<#
.SYNOPSIS
Collect one local ESP session after the operator has prepared the game and Sysmon.
.DESCRIPTION
No installation, elevation, game launch, ESP launch, or central transmission is
performed. Scenario and optional ON/OFF times are operator-supplied metadata;
they do not enable or disable ESP. Raw evidence remains local. A successful
collection is not, by itself, proof that ESP was used or calibration is complete.
.EXAMPLE
.\collect_replay.ps1 -Scenario normal -PlayerId player_042 -DurationSeconds 120
.EXAMPLE
.\collect_replay.ps1 -Scenario esp -PlayerId player_042 -DurationSeconds 120 -GamePid 1234 -ExportReplay
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('normal', 'esp')]
    [string]$Scenario,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_.-]{1,128}$')]
    [string]$PlayerId,

    [ValidateRange(5, 3600)]
    [int]$DurationSeconds = 120,

    [ValidateRange(0, 2147483647)]
    [int]$GamePid = 0,

    [Nullable[long]]$CheatOnMs,
    [Nullable[long]]$CheatOffMs,
    [string]$PythonExecutable = 'python',
    [switch]$ExportReplay
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$collectionStarted = $false
$sessionDirectory = $null

try {
    # Every prerequisite is checked before invoking the collector or writing files.
    if ($env:OS -ne 'Windows_NT') {
        throw 'This collector requires Windows.'
    }
    $principal = [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator privileges are required; no collection files were created.'
    }
    if ($PlayerId -in @('.', '..') -or $PlayerId.EndsWith('.') -or
        $PlayerId.Split('.')[0] -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
        throw 'PlayerId must be a safe, non-reserved shared identifier.'
    }
    $hasOn = $PSBoundParameters.ContainsKey('CheatOnMs')
    $hasOff = $PSBoundParameters.ContainsKey('CheatOffMs')
    if (($hasOn -and $null -eq $CheatOnMs) -or ($hasOff -and $null -eq $CheatOffMs)) {
        throw 'Supplied ON/OFF metadata must be an integer, not null.'
    }
    if (($hasOn -or $hasOff) -and $Scenario -ne 'esp') {
        throw 'ON/OFF metadata is allowed only with Scenario esp.'
    }
    foreach ($timing in @($CheatOnMs, $CheatOffMs)) {
        if ($null -ne $timing -and ($timing -lt 0 -or $timing -gt ([long]$DurationSeconds * 1000))) {
            throw 'ON/OFF metadata must fall within the requested session duration.'
        }
    }
    if ($hasOn -and $hasOff -and $CheatOffMs -lt $CheatOnMs) {
        throw 'CheatOffMs must not precede CheatOnMs.'
    }

    $espRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $repositoryRoot = [IO.Path]::GetFullPath((Join-Path $espRoot '..\..\..'))
    $configPath = Join-Path $espRoot 'config.example.json'
    $runPath = Join-Path $espRoot 'run.py'
    $exportPath = Join-Path $repositoryRoot 'ReplayAnalyzer\tools\export_esp_replay.py'
    $captureRoot = Join-Path $espRoot 'data\sessions'
    $replayRoot = Join-Path $repositoryRoot 'ReplayAnalyzer\replay-data\esp'
    foreach ($requiredFile in @($configPath, $runPath)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw 'The tracked collector or config.example.json is missing.'
        }
    }
    if ($ExportReplay -and -not (Test-Path -LiteralPath $exportPath -PathType Leaf)) {
        throw 'The tracked privacy-filtered replay exporter is missing.'
    }
    $example = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($example.game_executable -cne 'PenguinHotel-Win64-Shipping.exe' -or
        $example.response.mode -cne 'observe' -or $example.identity.enabled -ne $false -or
        $example.telemetry.enabled -ne $true -or $example.telemetry.root -cne 'data/sessions' -or
        $example.database_path -cne 'data/anti_esp.sqlite3') {
        throw 'config.example.json does not match the expected local observe-only capture settings.'
    }

    $pythonCommand = Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $pythonPath = $pythonCommand.Source
    $pythonCheck = @'
import json, struct, sys
try:
    import win32evtlog
    ok = sys.version_info >= (3, 11) and struct.calcsize('P') == 8
except Exception:
    ok = False
print(json.dumps({'ready': ok}))
sys.exit(0 if ok else 2)
'@
    $pythonResult = & $pythonPath -B -I -c $pythonCheck 2>$null
    if ($LASTEXITCODE -ne 0 -or -not (($pythonResult -join "`n" | ConvertFrom-Json).ready)) {
        throw 'Python 3.11+, a 64-bit interpreter, and pywin32 win32evtlog are required.'
    }
    $sysmonServices = @(Get-Service -Name 'Sysmon', 'Sysmon64' -ErrorAction SilentlyContinue)
    if (-not @($sysmonServices | Where-Object { $_.Status -eq 'Running' }).Count) {
        throw 'Sysmon must already be running; this script does not install or start it.'
    }
    $channelCheck = @'
import sys
import win32evtlog as evt
handles = []
try:
    channel = 'Microsoft-Windows-Sysmon/Operational'
    config = evt.EvtOpenChannelConfig(channel)
    handles.append(config)
    enabled = evt.EvtGetChannelConfigProperty(config, evt.EvtChannelConfigEnabled)
    if not bool(enabled[0] if isinstance(enabled, tuple) else enabled):
        raise RuntimeError('disabled')
    # Opening a query proves access without rendering or printing existing events.
    query = evt.EvtQuery(channel, evt.EvtQueryChannelPath | evt.EvtQueryReverseDirection,
                         '*[System[(EventID=10)]]')
    handles.append(query)
except Exception:
    sys.exit(2)
finally:
    for handle in reversed(handles):
        close = getattr(handle, 'close', None) or getattr(handle, 'Close', None)
        if close is not None:
            close()
'@
    & $pythonPath -B -I -c $channelCheck 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'The enabled Sysmon Operational channel must be accessible to pywin32.'
    }

    $gameName = 'PenguinHotel-Win64-Shipping'
    $games = @(Get-Process -Name $gameName -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -ceq $gameName })
    if ($games.Count -ne 1) {
        throw 'Exactly one PenguinHotel-Win64-Shipping.exe must already be running.'
    }
    $game = $games[0]
    if ($GamePid -ne 0 -and $game.Id -ne $GamePid) {
        throw 'The running game PID differs from the requested GamePid.'
    }
    $GamePid = $game.Id
    $gameStartTicks = $game.StartTime.ToUniversalTime().Ticks
    $sessionId = '{0}_{1}_{2}' -f $Scenario, [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss'),
        ([Guid]::NewGuid().ToString('N').Substring(0, 12))
    $sessionDirectory = Join-Path $captureRoot $sessionId
    if (Test-Path -LiteralPath $sessionDirectory) {
        throw 'The generated session directory already exists; collection was not started.'
    }

    Write-Host ('Preflight passed. Session={0}; GamePid={1}; Duration={2}s.' -f
        $sessionId, $GamePid, $DurationSeconds)
    Write-Host 'Scenario and ON/OFF times are metadata only. No ESP/game controls are executed.'
    $collectorArgs = @('-u', '-B', $runPath, '--config', $configPath, '--headless',
        '--session-id', $sessionId, '--player-id', $PlayerId, '--scenario', $Scenario,
        '--duration', $DurationSeconds.ToString(), '--central-telemetry', 'off')
    if ($hasOn) { $collectorArgs += @('--cheat-on-ms', $CheatOnMs.ToString()) }
    if ($hasOff) { $collectorArgs += @('--cheat-off-ms', $CheatOffMs.ToString()) }
    $health = [ordered]@{
        schema_version = 'meccha.capture-health.v1'
        sample_count = 0
        healthy_sample_count = 0
        insufficient_sample_count = 0
        last_status = $null
        last_observation_confidence = $null
    }
    $collectionStarted = $true
    # Only whitelist health fields. Do not print arbitrary evidence or paths.
    & $pythonPath @collectorArgs 2>$null | ForEach-Object {
        $line = [string]$_
        if ($line.StartsWith('{')) {
            try {
                $snapshot = $line | ConvertFrom-Json
                if ($snapshot.status -in @('LOW', 'REVIEW', 'HIGH', 'CRITICAL', 'INSUFFICIENT') -and
                    $snapshot.observation_confidence -is [ValueType] -and
                    $snapshot.observation_confidence -isnot [bool] -and
                    [double]$snapshot.observation_confidence -ge 0 -and
                    [double]$snapshot.observation_confidence -le 100) {
                    $health.sample_count += 1
                    $health.last_status = $snapshot.status
                    $health.last_observation_confidence = [double]$snapshot.observation_confidence
                    if ($snapshot.status -eq 'INSUFFICIENT') {
                        $health.insufficient_sample_count += 1
                    }
                    else {
                        $health.healthy_sample_count += 1
                    }
                    Write-Host ('Collector: {0}; observation_confidence={1}' -f
                        $health.last_status, $health.last_observation_confidence)
                }
            }
            catch {
                # Non-snapshot output is never interpreted as a healthy sample.
            }
        }
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'The collector exited unsuccessfully; its local session is not a valid replay.'
    }
    $gameAfter = Get-Process -Id $GamePid -ErrorAction SilentlyContinue
    if ($null -eq $gameAfter -or $gameAfter.ProcessName -cne $gameName -or
        $gameAfter.StartTime.ToUniversalTime().Ticks -ne $gameStartTicks) {
        throw 'The selected game process ended or changed during collection; do not export this session.'
    }

    $verifyCapture = @'
import base64, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from shared.schema import decode_event
def require(condition):
    if not condition:
        raise ValueError('invalid capture')
try:
    manifest_path = root / 'manifest.json'
    events_path = root / 'events.jsonl'
    require(not manifest_path.is_symlink() and not events_path.is_symlink())
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    require(manifest['schema_version'] == 'meccha.telemetry-session.v1')
    require(manifest['status'] == 'completed' and manifest['failure_reason'] is None)
    require(manifest['session_id'] == sys.argv[3])
    require(manifest['game_executable'] == 'PenguinHotel-Win64-Shipping.exe')
    require(manifest['producer']['name'] == 'meccha-esp-localguard')
    metadata = manifest['test_metadata']
    require(metadata['scenario'] == sys.argv[5])
    require(metadata.get('cheat_on_ms') == (None if sys.argv[6] == '-' else int(sys.argv[6])))
    require(metadata.get('cheat_off_ms') == (None if sys.argv[7] == '-' else int(sys.argv[7])))
    count = 0
    with events_path.open(encoding='utf-8-sig') as stream:
        for line in stream:
            if not line.strip():
                continue
            event = decode_event(line.encode('utf-8'))
            require(event['session_id'] == sys.argv[3] and event['player_id'] == sys.argv[4])
            require(event['module'] == 'esp' and event['evidence'].get('synthetic') is not True)
            count += 1
    require(type(manifest['event_count']) is int and manifest['event_count'] == count)
    raw_counts = {}
    for raw_path in sorted((root / 'raw').glob('*.jsonl')):
        require(not raw_path.is_symlink())
        raw_count = 0
        with raw_path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                require(record['session_id'] == sys.argv[3])
                target_pid = record.get('payload', {}).get('target_pid')
                require(target_pid is None or target_pid == int(sys.argv[8]))
                raw_count += 1
        raw_counts[raw_path.stem] = raw_count
    require(manifest.get('raw_counts', {}) == raw_counts)
    require(type(manifest['raw_event_count']) is int and manifest['raw_event_count'] == sum(raw_counts.values()))
    health = json.loads(base64.b64decode(sys.argv[9], validate=True))
    require(health['schema_version'] == 'meccha.capture-health.v1')
    # Diagnostic summary remains local and is not copied to public Replay.
    (root / 'capture_health.json').write_text(
        json.dumps(health, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    require(health['healthy_sample_count'] > 0)
    require(health['last_status'] in ('LOW', 'REVIEW', 'HIGH', 'CRITICAL'))
    print(json.dumps({'event_count': count, 'raw_event_count': sum(raw_counts.values())}))
except Exception:
    sys.exit(2)
'@
    # A nonempty sentinel preserves argument positions in Windows PowerShell 5.1.
    $onText = if ($hasOn) { $CheatOnMs.ToString() } else { '-' }
    $offText = if ($hasOff) { $CheatOffMs.ToString() } else { '-' }
    $healthJson = $health | ConvertTo-Json -Compress
    # PS 5.1 native argument quoting can remove JSON quotation marks.
    $healthEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($healthJson))
    $verifyResult = & $pythonPath -B -c $verifyCapture $sessionDirectory $repositoryRoot `
        $sessionId $PlayerId $Scenario $onText $offText $GamePid.ToString() $healthEncoded 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'The capture failed health, schema, identity, metadata, or event-count validation.'
    }
    $verified = $verifyResult -join "`n" | ConvertFrom-Json
    Write-Host ('Local capture verified: {0} common events, {1} raw records. Files: {2}' -f
        $verified.event_count, $verified.raw_event_count, $sessionDirectory)
    if ($ExportReplay) {
        & $pythonPath -B $exportPath '--source' $sessionDirectory '--output-root' $replayRoot `
            1>$null 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw 'Privacy-filtered replay export was rejected; the original local capture remains intact.'
        }
        Write-Host ('Privacy-filtered replay exported: {0}' -f (Join-Path $replayRoot $sessionId))
    }
    else {
        Write-Host 'Replay export was not requested. Raw evidence remains local.'
    }
    exit 0
}
catch {
    if ($collectionStarted) {
        Write-Host ('Local session artifacts, if created, remain at: {0}' -f $sessionDirectory)
    }
    Write-Error ('ESP replay capture stopped: {0}' -f $_.Exception.Message) -ErrorAction Continue
    exit 2
}
