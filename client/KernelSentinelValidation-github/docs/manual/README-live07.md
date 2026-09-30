# 07번 실제 커널 모듈 코드 해시 검사

`LiveCode07.py`와 함께 제공된 `LiveEntry06.py`를 VM의 `$env:USERPROFILE\Desktop\KernelSentinelValidation`에 복사합니다. 같은 폴더 아래에 기존 `KernelSentinel\agent`가 있어야 합니다. 기존 KernelSentinel의 드라이버와 에이전트 코드는 수정하지 않습니다.

이 검증은 06번에서 확인한 `NtQuerySystemInformation`의 동등한 XOR 바이트 변경을 자극으로 사용합니다. 변경 전에 설치된 KernelSentinel의 코드 스캐너가 NT 커널 실행 섹션의 SHA-256 기준값과 정상 비교 결과를 내야 합니다. 변경 중에는 같은 모듈의 캐시 기준 해시를 유지하면서 다른 현재 해시와 `flags=3` 및 기존 에이전트의 `kernel_executable_sections_changed_since_first_scan` 판정이 나와야 합니다. 원복 후에는 원래 바이트, 원래 해시, `flags=1`, 판정 해제가 확인돼야 PASS입니다.

스캐너는 호출할 때마다 로드된 모듈 하나를 순서대로 검사합니다. 대상 NT 커널 모듈에 도달할 때까지 수백 회 호출할 수 있습니다. 다른 모듈의 검사 실패를 대상 모듈 성공으로 처리하지 않습니다. 대상 모듈 자체가 실패하면 중단합니다.

## 실행

1. 테스트 VM 상태를 되돌릴 수 있게 VMware 스냅샷을 준비합니다. 커널 코드 변경 실험입니다.
2. 기존 `watch`와 `Run.ps1` 실행은 종료하고 KernelSentinel 드라이버는 로드합니다. VM에서 `sc.exe query KernelSentinel`로 `RUNNING`을 확인할 수 있습니다.
3. 호스트 WinDbg에서 `Debug > Break` 후 `x nt!NtQuerySystemInformation`으로 이번 부팅 주소를 확인하고 `g`로 재개합니다.
4. VM 관리자 PowerShell에서, 실제 주소를 넣어 실행합니다. 예시 주소는 이전 2026-09-30 부팅 세션용입니다.

```powershell
Set-Location "$env:USERPROFILE\Desktop\KernelSentinelValidation"
python -u .\LiveCode07.py --function-address 0xfffff8003d8c9cb0
```

5. `BASELINE_OK`, `WAIT_MUTATION`이 나오면 VM 창을 그대로 둡니다. 호스트 WinDbg의 Debug > Break에서 출력된 `.expr`과 `.if` 명령을 실행합니다.
6. `WRITE_VERIFIED`와 `45 31 d2 xor r10d,r10d`를 확인합니다. WinDbg에서 `g`, VM 창에서 Enter를 누릅니다.
7. `CHANGE_OBSERVED`가 나오면 지체하지 않고 호스트 WinDbg의 Debug > Break에서 원복 명령을 실행합니다. `WRITE_VERIFIED` 또는 `ALREADY_RESTORED`, 원래의 `45 33 d2`를 확인합니다. WinDbg에서 `g`, VM 창에서 Enter를 눌러 원복 후 해시를 검사합니다.

WinDbg에서 ABORT, WRITE_FAILED 또는 명령 오류가 나오면 예상하지 못한 주소나 바이트를 직접 덮어쓰지 않습니다. 실행 폴더의 `RESTORE_WINDBG.txt`는 중단 시 원복 명령입니다. 실제 원복이 확인되기 전에는 결과가 PASS가 될 수 없습니다.

실측 결과는 `runs\live07_...\report.json`, `report.md`, `raw_events.jsonl`에 저장합니다. 이 시험은 설치된 KernelSentinel의 코드 해시 센서를 검증하며, 모든 커널 모듈 또는 모든 코드 변경 형태의 검출을 보장하지 않습니다. 일반 `Run.ps1` 통합은 06·07·09·10번 실측을 마친 후 진행할 예정입니다.
