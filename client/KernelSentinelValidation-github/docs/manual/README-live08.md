# 08번 실제 디스패치 포인터 검증

이 폴더의 LiveDispatch08.py를 VM의 KernelSentinelValidation 폴더에 복사합니다.
같은 폴더 아래에 기존 KernelSentinel/agent가 있어야 합니다. 기존 파일을 덮어쓸 필요는 없습니다.

## 실행

1. 기존 agent.main watch와 Run.ps1 실행을 종료합니다. KernelSentinel 드라이버는 실행 상태로 둡니다.
2. 호스트 WinDbg에서 현재 부팅의 `x KernelSentinel!KsDispatch`, `x KernelSentinel!KsOpenClose` 주소를 확인합니다.
3. WinDbg에서 `g`로 VM을 재개합니다.
4. VM 관리자 PowerShell에서 실행합니다. 아래 주소는 2026-09-29 대화에서 확인한 현재 세션용입니다.

```powershell
Set-Location "$env:USERPROFILE\Desktop\KernelSentinelValidation"
python -u .\LiveDispatch08.py --original 0xfffff8016f9924b0 --replacement 0xfffff8016f992f10
```

재부팅하거나 드라이버를 다시 로드했으면 두 주소를 다시 조회하고 바꿔야 합니다.

5. BASELINE_OK, WAIT_MUTATION이 표시되면 검증 프로그램을 그대로 둡니다.
6. 호스트 WinDbg에서 Debug > Break를 누르고 프로그램이 출력한 `.expr`과 `.if` 두 줄을 실행합니다.
   출력된 포인터가 replacement 주소인지 확인합니다. 오류 또는 ABORT면 임의로 주소를 바꾸지 않습니다.
7. WinDbg에서 `g`, VM 검증 창에서 Enter를 누릅니다.
8. CHANGE_OBSERVED 또는 CHANGE_NOT_VERIFIED 뒤 출력되는 원복 명령을 호스트 WinDbg에서 Break 후 실행합니다.
   원복 후 포인터가 original 주소인지 확인하고 `g`를 입력합니다.
9. VM 검증 창에서 Enter를 눌러 원복 검증을 완료합니다.

## 실제 검증 범위

- 실제 설치된 KernelSentinel 장치를 한 번 열어 같은 핸들을 끝까지 유지합니다.
- CREATE 포인터만 이미 존재하는 KsOpenClose로 변경합니다. 변경 기간에 드라이버를 중지하거나 다시 로드하지 마세요.
- 드라이버의 진단 IOCTL 응답에서 CREATE 주소, 고정 기준값, 변경 비트 1을 3회 확인합니다.
- 기존 agent.analysis.findings에서 디스패치 변경 판정이 나오는지 확인합니다.
- CLOSE/DEVICE_CONTROL 포인터와 모든 기준값은 변경되지 않아야 합니다.
- 원복 후 세 포인터의 원래 값, 변경 비트 0, 변경 판정 해제를 3회 확인해야 최종 PASS입니다.
- 이것은 함수 포인터 변경 탐지 테스트입니다. 대체 함수 실행, 모듈 외부 포인터 판정, 06/07/09/10은 검증하지 않습니다.

## 보고서와 중단 처리

결과는 runs/live08_날짜/report.json, report.md, raw_events.jsonl에 저장합니다.
처음부터 INCONCLUSIVE 보고서를 저장하므로 중도 종료는 PASS가 되지 않습니다.
Python 프로그램은 커널 메모리를 쓰지 못하므로 자동 원복을 보장하지 않습니다.
변경 후 중단되면 같은 실행 폴더의 RESTORE_WINDBG.txt를 호스트 WinDbg에서 사용하고 포인터를 확인하세요.
명령은 현재 부팅의 심볼 주소와 슬롯 값을 확인한 뒤 쓰기를 허용합니다. ABORT가 나오면 확인되지 않은 주소에 쓰지 마세요.

일반 Run.ps1의 과거 NOT_RUN 결과는 변경하지 않습니다. 이번 실제 실행의 08번 결과는 별도 live08 보고서에 기록됩니다.
호스트에서 검증한 것은 판정 로직과 오류 경로입니다. VM에서 위 순서를 완료하기 전에는 실제 08번 성공으로 간주하지 않습니다.
