# Approved defense access classification — 2026-10-07

Base: `ce84650240861d4225496304a3d89a00ac022d98`. Changes are confined to
`client/kernel_sentinel`; Launcher, Shared, driver binary/source, Receiver and B
policies are unchanged. Public Event remains seven fields and module remains
`kernel_sentinel`. The existing Launcher t0 clock and Shared lifecycle/ACK contract
are preserved. A local queued receipt is still not a server ACK.

## Classification and approval boundary

Only sensitive handle findings are considered for exemptions. Non-sensitive
query handles, including the collector's own callback probe, remain raw
observations and do not require an exemption. Every original driver record is
written before classification; it is not overwritten with an approval label.

An exemption requires all of:

- Current-session registry, not stopping; exactly one entry for the caller PID.
- Registry and driver caller exact Windows FILETIME creation times agree with a
  live process; the live target agrees with the retained game's PID/FILETIME.
- Live Launcher PID/FILETIME, source path, working directory and explicit
  `--session`/`--player` match. A restarted defense module can be approved only
  through its new registered/live generation; cached identities are not reused.
- Actual native executable path and on-disk SHA-256 agree with the collector's
  running Python runtime. A venv registry stub is accepted only when it is the
  collector's known interpreter; actual module/Launcher runtime is also checked.
- Live argv matches registered argv (only the venv executable slot may differ),
  the reviewed entry script or `-m` target matches, and live cwd is the repository
  root. Module session/player and explicit game PID flags must match.
- All reviewed source hashes and the Python source inventory match the fixed
  approval manifest. Unknown modules/source revisions are not auto-approved.
- Original requested, before and granted access masks are present and entirely
  inside the reviewed task's rights. Thread, duplicate-handle, restricted and
  incomplete operations receive no exemption.
- Live generations and the registry are rechecked after validation.

The manifest pins 82 source files from the stated baseline. It contains no test
PID, creation time or player allowlist. Do not regenerate its hashes from arbitrary
local files to silence mismatches. A new deployment revision needs review and a
new approval manifest. Deploy reviewed code/policy/runtime in a directory whose
ACL prevents untrusted writes; the existing collector deployment is the trust
anchor. These checks do not attest the process's loaded memory, Python dependencies,
call stack or absence of injection. Approval means an observed access falls
inside the reviewed defense scope, not that the actor can never be compromised.

## Reviewed access scopes

| Registered module | Allowed process mask | Basis and remaining limits |
|---|---:|---|
| hide_anywhere | `0x101010` | `mecha_logger.py` Target query/synchronize and Observer read/query; no write/terminate/inject/duplicate |
| esp | `0x1040` | `windows_api.py`, `sensors/handle_sensor.py` limited query and source-process DUP_HANDLE; no exemption for actual duplicate-handle operations or thread access |
| autopaint | `0x1000` | `gzz_anticheat/windows_sensor.py` limited identity query; no exemption for broader access |
| module_integrity | none | Toolhelp enumeration is read-only in purpose, but its implicit OS/psutil access masks are not verified; **remains unresolved** |

`PROCESS_ALL_ACCESS`, even if subsequently reduced to read-only grants, is outside
these scopes. The prior sensitive-access score remains. Identity/scope mismatches
are `evidence.access_approval.classification=unresolved`, with a specific reason;
they are not presented as approved NORMAL observations. Sensitive access awaiting
approval is `SUSPICIOUS`, not proof of cheating. Verification exceptions add an
explicit error and set measurement_valid=false. Valid unrelated positives remain
scored even when another check fails.

A verified exemption produces `approved_defense_module_access`, score 0, NORMAL,
with actor identity, runtime/source attestations, allowed task/mask and approval
reason inside evidence. sensor_cycle uses the same filtered findings, resets all
cycle state, keeps other positives and errors, and includes bounded identity
summaries plus exact approval counts. An approved-only cycle is valid NORMAL 0;
its scope is the observed cycle, not a claim that every possible sensor passed.

## Prerequisite without Launcher changes

Live argv/cwd inspection uses psutil; native GetProcessTimes supplies exact
FILETIME (psutil floating-point creation times are never used).

```powershell
# Use the same interpreter/venv as Launcher.
python -m pip install -r client/kernel_sentinel/requirements-approval.txt
```

If this dependency or live inspection is unavailable, approval fails closed:
observed access is not exempted, the error is retained, and monitoring continues.
Adding this prerequisite to team-wide installation is a Launcher owner request;
this patch does not change their installation flow.

## Verification actually performed

Linux offline/synthetic tests plus a real local loopback HTTP server; **no Windows
kernel driver, game or full Launcher session was executed here**.

- Regression suite: 98 tests pass, including existing telemetry/driver setup tests.
- Synthetic identities: approval, caller/target/Launcher PID reuse, stale/stopping
  registry, unregistered/unknown module, live command/cwd/script/session mismatch,
  wrong executable, modified/added source, unverified rights, missing data,
  excessive original/before/granted rights, failed handle request, thread/duplicate,
  generation changing during validation and failures after prior approval.
- Common-event and cycle integration: raw unchanged; common seven fields; approved
  0; excess access still positive; mixed approval plus independent valid positive
  still scored; mixed positive plus inspection failure retains both; next healthy
  cycle has no stale positive reasons. Non-sensitive callback queries do not
  create unresolved approval noise.
- Actual loopback path: local common log → Shared queue → HTTP Receiver durable
  storage ACK → Scoring store → authenticated Dashboard events API. Stored payloads
  equal local payloads; Launcher timestamp basis preserved. NORMAL, DETECTED,
  SUSPICIOUS and ERROR evidence is retained in Dashboard API results. Flush succeeds,
  acknowledged count equals sent events, pending=0/failed=0, sender shuts down.

The Dashboard API preserves these states; this is not a browser UI verification
or proof of an operational server deployment. There is still no dedicated
`kernel_sentinel` B Scoring policy; no final normal/cheat verdict is certified by
this patch or its tests.

## Remaining team/Windows verification

1. Module owners confirm observed excess masks and module_integrity's actual OS
   access requirements; reduce unnecessarily broad OpenProcess calls or provide
   reviewed bounded requirements. Do not enable ALL_ACCESS exemptions.
2. Launcher owner supplies psutil prerequisite if missing, confirms the trusted
   deployment revision and explicit session/player launch args; no registry/schema
   or Shared changes are required for this patch.
3. On Windows 10 19045, run a new complete normal Launcher session, same game and
   runtime, with observe mode. Confirm approved scopes, out-of-scope signals,
   SelfDefense direct queries and watchdog restarts, startup/shutdown and latency/
   ring coverage. Do not weaken signing, Defender or OS security for this test.
4. Run authorized isolated positive controls. Confirm local events, real Shared
   ACK, operational server storage, Dashboard API/UI states, normal module exit,
   pending/failed and shutdown. Runtime driver/OS rights and registry race behavior
   remain unverified here.
5. B/Dashboard owners separately resolve dedicated kernel_sentinel scoring policy
   and final/status presentation. No scoring-policy change is bundled.

Apply the provided Git patch using `git am` in a clean branch based on the stated
baseline, or transplant only its kernel_sentinel changes after review. On another
source revision, source mismatches intentionally remain unresolved until reviewed;
this patch must not silently approve a newer main branch.
