# Approved driver and live validation gate

## Artifact status

This source delivery is KernelSentinel driver protocol v1 / agent base 0.2.3 plus
Launcher integration. No built, signed, approved SYS is supplied. Accordingly an
approved binary version, actual SHA-256, signer thumbprint and successful OS build
matrix **cannot be asserted**. Never use a placeholder SHA-256 as approval.

The release owner must record the following for each signed approved artifact:

- source commit, build toolchain/SDK/WDK and configuration;
- signed SYS filename, exact SHA-256, signer identity/thumbprint;
- actual Windows edition/build/UBR and security configuration used;
- signature validation, service start, device status/capabilities and smoke result;
- approver and approval date, public artifact download location.

General implementation target is Windows 10 2004+ x64 (ExAllocatePool2).
The private-layout kernel thread sensor is restricted to Windows 10 build 19045;
Launcher disables it on other builds. This is source compatibility intent, not a
certified support matrix. Validate the exact target OS before approving it.

## Source-only PC

Install VS C++ and integrated matching SDK/WDK; build using scripts/Build.ps1.
Obtain a properly signed approved SYS from the release owner, or submit the build
for signing/approval through the team's established process. Do not bypass signature
verification, Defender or OS protections. A successful build alone is not approval.
Set GZZ_KERNEL_DRIVER_PATH and GZZ_KERNEL_DRIVER_SHA256 for that approved binary.
Automatic setup verifies hash/signature, installs or reuses the service, then starts it.
RUNNING alone does not prove the device protocol works: require driver_device_open
and driver_status_query startup stages to report ok as well.

## Failure diagnosis

Console stderr and runs/<session>.startup.jsonl contain begin/ok/failed, error_type,
winerror and errno. Stages include config_read, target_process_query,
driver_device_open, driver_status_query, local_log_and_shared_init and approved_driver_setup.
WinError 2 at config_read points to policy path; at device_open it points to device
absence/driver load/initialization. Inspect service query and system event records;
do not conclude a driver cause from the error number alone.

## Live run matrix — pending Windows execution

1. Verify approved service RUNNING, device/status ok and observe mode session_start.
2. Run the full Launcher on the same game PC with all intended components and common t0.
3. Run normal game + Steam/overlay and SelfDefense; check no false sensitive-rights signals
   arise from QUERY_LIMITED_INFORMATION|SYNCHRONIZE or QUERY_INFORMATION|SYNCHRONIZE.
4. Use the existing tools/probe_handles.py in the approved test environment to request
   the designed sensitive rights; correlate raw pre/post, granted access and common reason.
   Inspect the tool and use the actual game PID; observe does not remove rights.
5. Verify full Launcher registry PID/create_time, heartbeat and component state. Collector
   restart remains disabled because the output directory is exclusive; do not simulate
   restart by overwriting a previous run. A new Launcher session requires a new output path.
6. Stop the game/collector gracefully; verify policy disarm, Shared flush/shutdown,
   unchanged OS/Defender settings and server event IDs. SYS remains loaded until explicitly stopped.
7. Check Receiver durable events, Scoring state and Dashboard alias/heartbeat independently.
   No kernel_sentinel-specific B policy exists in the tested server snapshot; final
   judgment is not guaranteed and policy registration is a B-team prerequisite.

Evidence to attach: approved artifact manifest, source commit, OS build, startup JSONL,
raw/common logs, feature_status, service query, Launcher logs/registry and token-redacted API responses.

## What is verified here

Portable tests confirm the two SelfDefense query masks are outside sensitive masks,
observe-mode source semantics, failure-stage diagnostics and synthetic positive rules.
Shared/common t0 + real loopback Receiver/Scoring storage integration was executed.
No full Windows Launcher coexistence, driver load, real positive event, restart conflict
or final Dashboard verdict has been executed here. PID/create-time registration based
SelfDefense exemption is still not implemented; no blanket python.exe exemption is introduced.
