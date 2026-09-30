# Optional host debugger bridge

This is the source of the host helper used by `Run.ps1 -FreshValidation`. It is experimental and has not been verified end-to-end on the user's VM. No executable, KDNET key, or `bridge.json` is included in this repository.

Build `KsDbgBridge.vcxproj` for Release/x64 with Visual Studio C++ and Windows Debugging Tools installed. The project targets Windows SDK 10.0.28000.0 and writes `bin/KsDbgBridge.exe`; adjust the SDK/toolset in Visual Studio if your installed version differs. `StartHostBridge.cmd` expects that output in `bin/`.

The host helper needs an isolated test VM with KDNET already configured. Close WinDbg before launching it. The launcher requests the KDNET key interactively, opens the VMnet8 bridge port, and generates a local `bridge.json`. Copy that JSON file to the VM repository root; keep it out of Git. The VM's fresh runner stops before changing kernel state if the bridge preflight fails.

Only use the mutation tests with a recoverable VM snapshot. A PASS requires both KernelSentinel's changed measurement and verified restoration of the original bytes or pointer.
