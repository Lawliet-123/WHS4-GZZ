# Integration validation

Base: WHS4-GZZ main `1cb87cb` (2026-10-07 checked snapshot).

- 84 KernelSentinel tests: PASS. Includes existing protocol/analysis/sensor/portable C tests.
- 8 new integration tests within the 77: common t0, normal/error cycles, local-before-send,
  canonical-ID facade, dedicated outbox requirement, installer input validation,
  installer argv dispatch (mocked), real loopback Shared/Receiver/Scoring storage.
- 3 additional tests: four startup error stages preserve WinError; SelfDefense query
  masks are not sensitive-rights findings; successful positive and failed request differ.
- Bundled manifest default-path/hash selection with no environment configuration: PASS.
- Mocked test setup: reboot-required halts execution, setup failure stops install,
  already effective test mode continues. No real BCD or certificate stores were modified.
- Launcher resolved argv/path/config/PYTHONPATH/admin/game/restart checks: PASS.
- Python compileall and git diff --check: PASS.
- Full Launcher registration test requires Windows msvcrt and was not executed on Linux.
- Driver installation PowerShell was reviewed, not executed. Installer test mocks process invocation.
- WDK build, driver signing/loading, real game and final Dashboard verdict: NOT VERIFIED.

The real loopback test stores NORMAL 0, a synthetic configured-name 2, and cycle 2 unchanged.
No malicious driver or cheat is executed. No production server is contacted.
The existing server has no audited kernel_sentinel policy; no final cheat verdict is asserted.
