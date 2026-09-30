# 06번 실제 커널 함수 진입부 검사

`LiveEntry06.py`를 VM의 `$env:USERPROFILE\Desktop\KernelSentinelValidation`에 복사합니다. 같은 위치에 기존 `KernelSentinel\agent`가 있어야 합니다. 기존 드라이버와 에이전트 파일은 교체하지 않습니다.

현재 대상은 KernelSentinel의 두 번째 검사 함수 `NtQuerySystemInformation`입니다. 2026-09-29 디버거 출력에서 함수 시작 주소는 `fffff801`528c9cb0`이며, 시작 지점 +6의 `45 33 d2`를 `45 31 d2`로 바꿉니다. 두 바이트열은 모두 `xor r10d,r10d`로 해석됩니다. +7의 `33` 한 바이트만 `31`로 바꾸고 되돌립니다. 명령어와 32바이트 전체가 현재 빌드와 일치하지 않으면 디버거 명령이 쓰기를 거부합니다.

## 실행

1. 테스트 VM의 상태를 되돌릴 수 있도록 VMware 스냅샷을 준비합니다. 커널 코드 변경 실험입니다.
2. 기존 `watch`와 `Run.ps1` 실행을 종료하고 KernelSentinel 드라이버는 로드한 채 둡니다.
3. 호스트 WinDbg에서 `Debug > Break` 후 `x nt!NtQuerySystemInformation`을 실행해 현재 주소를 확인하고, `g`로 재개합니다.
4. VM 관리자 PowerShell에서 다음을 실행합니다. 재부팅했다면 주소를 새 주소로 바꿉니다.

```powershell
Set-Location "$env:USERPROFILE\Desktop\KernelSentinelValidation"
python -u .\LiveEntry06.py --function-address 0xfffff801528c9cb0
```

5. `BASELINE_OK`와 `WAIT_MUTATION`이 나오면 VM PowerShell을 그대로 둡니다. 호스트 WinDbg에서 `Debug > Break` 후 프로그램이 출력한 `.expr /s masm`과 긴 `.if` 명령을 실행합니다.
6. `WRITE_VERIFIED`와 `45 31 d2 xor r10d,r10d`가 출력됐는지 확인합니다. `ABORT`, `WRITE_FAILED` 또는 디버거 오류가 나오면 다음 단계로 넘어가지 않습니다.
7. WinDbg에서 `g`를 입력하고 VM PowerShell에서 Enter를 누릅니다.
8. 변경 감지 결과가 출력되면 곧바로 호스트 WinDbg에서 `Debug > Break`를 누른 후 검증 프로그램이 출력한 원복 명령을 실행합니다. `WRITE_VERIFIED`와 `45 33 d2 xor r10d,r10d`를 확인합니다.
9. WinDbg에서 `g`를 입력하고 VM PowerShell에서 Enter를 눌러 원복 후 진단 결과를 수집합니다.

중간에 프로그램이 종료되면 해당 실행의 `runs\live06_...\RESTORE_WINDBG.txt`에 원복 명령이 있습니다. 실제 원복 확인 전에는 결과가 PASS가 될 수 없습니다. 잘못된 주소나 예상과 다른 32바이트가 있으면 명령은 ABORT합니다. 절대로 예상하지 못한 바이트를 임의로 덮어쓰지 마세요.

## 판정 범위

실제 설치된 KernelSentinel의 진단 IOCTL을 동일한 장치 핸들로 읽습니다. 변경 전에는 4개 진입부 모두 기준값과 일치해야 합니다. 변경 중에는 두 번째 진입부의 한 바이트만 달라지고 변경 비트 `2`, 기존 `agent.analysis.findings`의 `kernel_entry_bytes_changed_since_driver_load` 판정이 3회 나와야 합니다. 원복 후에는 원래 32바이트, 변경 비트 `0`, 판정 해제가 3회 확인되어야 PASS입니다.

결과는 별도 `runs\live06_...\report.json`, `report.md`, `raw_events.jsonl`에 저장합니다. 이는 4개 함수 중 선택한 하나에 대한 실측 시험입니다. 일반 `Run.ps1`의 항목 06은 모든 실측 시험을 마친 뒤 통합할 예정입니다.
