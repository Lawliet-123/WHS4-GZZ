# 09번 실제 드라이버 목록 불일치 검증

이 시험은 이전 05번에서 사용한 서명된 `KsValidationProbe.sys`를 실제로 로드합니다. 정상 PSAPI와 KernelSentinel의 AuxKlib 목록에 모두 나타나는지 확인한 다음, **이 실행기 프로세스가 받는 PSAPI 결과에서 그 드라이버 한 개만 제거**합니다. KernelSentinel의 기존 `CrossView.compare`가 실제 AuxKlib 조회 사이에 낀 PSAPI 조회 3회에서 목록 불일치를 보고하는지 검사합니다. 마지막으로 정상 PSAPI 결과와 서비스 정리를 확인합니다.

이는 `09.live_cross_view_discrepancy` 검증입니다. 드라이버를 커널에서 숨기거나 두 목록 모두에서 숨기는 상황을 만들지 않으므로 `09.live_hidden_driver` 전체를 PASS로 주장하지 않습니다. 보고서에도 이 범위를 명시합니다.

## VM 실행

1. 이 폴더의 `LiveCrossView09.py`를 VM의 `$env:USERPROFILE\Desktop\KernelSentinelValidation`에 복사합니다. 기존 `KernelSentinel\agent`, `validation\probe_service.py`, `validation\driver_load.py`, 서명된 `bin\driver\KsValidationProbe.sys`가 그 루트 아래에 있어야 합니다.
2. 관리자 PowerShell에서 KernelSentinel 서비스가 실행 중인지 확인합니다. 기존 `watch`나 `Run.ps1` 세션은 종료합니다. `KsValidationProbe` 서비스가 이미 있으면 이 실행기는 덮어쓰지 않고 중단합니다.

```powershell
Set-Location "$env:USERPROFILE\Desktop\KernelSentinelValidation"
sc.exe query KernelSentinel
sc.exe query KsValidationProbe
python -u .\LiveCrossView09.py
```

`KernelSentinel`이 `RUNNING`이 아니면 `sc.exe start KernelSentinel`로 시작한 뒤 실행합니다. `KsValidationProbe` 조회는 1060(서비스 없음)이 정상입니다. `PASS`가 나오려면 시험 드라이버가 두 원본 목록에 나타나고, 필터링한 PSAPI 결과만 그 드라이버를 누락하며, 기존 분석기가 3회 지속 이벤트를 내고, 원본 결과와 서비스 정리가 확인돼야 합니다.

실행 결과는 `runs\live09_...\report.json`, `report.md`, `raw_events.jsonl`에 남습니다. 오류가 나면 마지막 `Reports:` 경로의 보고서와 콘솔 출력을 보내 주세요.
