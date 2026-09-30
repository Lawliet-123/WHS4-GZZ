# 10번 실제 모듈 밖 시스템 스레드 출처 검사

이 패키지는 사용자 테스트 VM용 시험 드라이버 `KsOutsideThreadProbe.sys`를 포함합니다. 드라이버는 실행 가능한 비페이지 풀의 12바이트 시작 코드에서 스레드를 시작하고, 그 코드는 드라이버 안의 정상 대기 함수로 이동합니다. 드라이버의 읽기 전용 IOCTL은 스레드 ID, 시작 주소, 시작 코드 바이트만 반환합니다. 기존 KernelSentinel의 스레드 센서가 이 스레드를 실제로 관측하고 3회 지속 판정하는지를 검사합니다.

## 준비

1. Windows 10 19045 x64 테스트 VM 스냅샷을 준비합니다. 이 시험은 별도 커널 드라이버를 로드합니다.
2. ZIP의 `LiveOutsideThread10.py`, `KsOutsideThreadProbe.sys`, `KsOutsideThreadProbe.pdb`, `Sign-OutsideThreadProbe.ps1`을 VM의 `$env:USERPROFILE\Desktop\KernelSentinelValidation`에 복사합니다. 원본 `KernelSentinel\agent`와 `validation\probe_service.py`는 같은 루트 아래에 있어야 합니다.
3. VM에서 관리자 PowerShell을 열고 KernelSentinel 드라이버가 실행 중인지 확인합니다. 중지돼 있다면 시작합니다. 다른 `watch`나 `Run.ps1` 세션은 종료합니다.

```powershell
Set-Location "$env:USERPROFILE\Desktop\KernelSentinelValidation"
sc.exe query KernelSentinel
sc.exe start KernelSentinel
```

이미 `RUNNING`이라면 `start`는 생략합니다.

4. 이전 05번 시험에 사용한 `CN=KernelSentinel Validation VM Test Only` 인증서가 VM의 LocalMachine/My, Root, TrustedPublisher에 남아 있어야 합니다. 다음은 새 인증서를 만들거나 부팅 설정을 바꾸지 않고 이 인증서로 시험 SYS만 서명합니다.

```powershell
.\Sign-OutsideThreadProbe.ps1
```

서명 결과 `Valid`를 확인하고 실행합니다.

```powershell
python -u .\LiveOutsideThread10.py
```

## 완료 조건

실행기는 사용 중인 KernelSentinel 장치를 열고, 시험 서비스가 이미 있으면 덮어쓰지 않고 중단합니다. 본인이 생성한 서비스만 시작·중지·삭제합니다. 시험 드라이버의 스레드 ID와 시작 주소를 직접 읽고, KernelSentinel의 실측 스레드 스냅샷과 양쪽 커널 모듈 목록을 대조합니다. 동일 스레드의 동일 생성 시각과 시작 주소가 모듈 범위 밖으로 3회 연속 기록되고 기존 분석기의 판정이 나타나야 합니다. 시험 서비스를 중지·삭제한 후 모듈이 목록에서 사라져야 PASS입니다.

결과는 `runs\live10_...\report.json`, `report.md`, `raw_events.jsonl`에 남습니다. 커널이 풀에서 실행하는 시작 코드를 허용하지 않거나 스레드 API가 다른 주소를 보고하면 PASS로 기록하지 않습니다. 검사 범위는 시작 주소이며 현재 실행 위치를 의미하지 않습니다.
