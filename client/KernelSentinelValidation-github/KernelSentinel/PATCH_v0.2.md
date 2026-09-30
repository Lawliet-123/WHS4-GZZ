# KernelSentinel 0.2 — 커널 스레드 출처 및 콜백 동작 검사

이 패키지는 캐시·Windows 10 호환성 패치를 포함한 **전체 소스**다.
SYS는 포함하지 않는다. 이번 버전의 WDK 빌드·VM 로드·실제 치트 검증은 미실시다.
이전 버전에서 성공한 VM 검사를 이번 버전의 성공으로 간주하지 않는다.

## 무엇이 달라졌나

| 추가 경로 | 수집 및 판단 | 실제 적용 가능한 상황 | 한계 |
|---|---|---|---|
| 커널 스레드 출처 | 커널에서 전체 프로세스의 스레드 ID를 조회하고 PsIsSystemThread로 확인. 참조한 스레드의 생성 시각과 종료 상태를 확인한 뒤 시작 주소 조회. 전후 모듈 목록이 같을 때만 대조 | 로드 목록에 없는 코드에서 시작한 지속적인 시스템 스레드가 있는 경우 | 스레드를 만들지 않는 코드, 정상 모듈 안의 시작점, 시작점 위장, 목록에서 누락된 스레드, 기존 스레드 탈취는 놓칠 수 있음 |
| 지속성 판정 | PID/TID/생성 시각/시작 주소가 동일한 항목이 3회 연속 목록 밖이면 점수 3 | 일시적 조회 변화와 구별할 근거 확보 | 악성 확정 아님. 현재 RIP/스택을 검사하지 않음 |
| OB 콜백 동작 점검 | 수집기가 보호 프로세스에 QUERY_LIMITED_INFORMATION 핸들을 요청하고 즉시 닫음. 자기 요청의 성공 pre/post 이벤트 쌍 확인 | 콜백 무력화 또는 이벤트 전달 중단의 징후 | 콜백 테이블 전체 검증 아님. 시스템 부하·수집 장애와 별도 구분 필요. 스푸핑을 막지 못함 |
| 오탐·누락 관리 | 스냅샷 실패·용량 제한·조회 실패·로그 손실을 별도 기록. 다른 스레드 경고가 중복 억제로 사라지지 않도록 키 보완 | 재현 가능한 증거 수집 | 로그 손실을 치트 점수로 가산하지 않음 |

기존 실행 코드 SHA-256 검사, 자체 IRP 포인터 검사, 핸들 정책, 이름·해시 규칙도 유지한다.
기본 해시 정책은 비어 있다. 알려진 치트 DB를 자동 탑재한 버전이 아니다.
`observe`에서도 새 탐지 경로는 작동한다. `enforce`는 기존의 일부 핸들 권한 제한이며 커널 직접 메모리 접근 차단이 아니다.

## 구현 범위와 호환성

- 새 native 스냅샷 해석은 **Windows 10 22H2 x64 / 빌드 19045 전용**이다. 다른 빌드는 기능을 사용 가능하다고 광고하지 않는다.
- 런타임에 ZwQuerySystemInformation / ZwQueryInformationThread를 해석하고, 누락 시 비활성화한다.
- 고정 ETHREAD/KTHREAD 내부 오프셋을 읽지 않는다. class-5 반환 버퍼의 빌드 한정 ABI를 사용하고 길이·정렬·개수를 검사한다.
- 스냅샷 할당 최대 16 MiB, 시스템 스레드 결과 최대 4096개, 스레드 순회 예산 2초다. 제한 도달은 실패/부분 수집으로 표시한다.
- 드라이버는 주소를 전달받아 임의 메모리에 접근하는 IOCTL을 추가하지 않았다.
- NMI/APC 스택 워킹, 다른 드라이버 디스패치 순회, 프로세스 attach 검사, 취약 드라이버 악용 차단은 이번 패치에 없다.
- 같은 커널 권한에서 센서·OS 조회를 조작하는 공격을 완전히 배제할 수 없다.

## 적용 순서

1. 빌드 PC에서 이 전체 소스를 새 폴더에 푼다. 이전 파일 일부만 섞지 않는다.
2. `KernelSentinel.sln`을 열어 x64 다시 빌드하거나 아래 명령을 프로젝트 폴더에서 실행한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Build.ps1"
```

3. VM의 기존 수집기를 Ctrl+C로 종료한다. VM 스냅샷을 남긴 뒤 관리자 PowerShell에서 드라이버를 중지한다.

```powershell
sc.exe stop KernelSentinel
sc.exe query KernelSentinel
```

4. STOPPED 확인 후 새 SYS 및 이 패키지의 `agent`, `config`, `tools`, `scripts`를 VM 프로젝트 폴더로 복사한다. 기존 로그는 보존하고, 개인 정책이 있으면 config를 백업해 병합한다.
5. 새 SYS 경로로 설치한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Install.ps1" -SysPath ".\KernelSentinel.sys"
```

6. RUNNING 확인 후 아래 1234를 실제 게임 PID로 바꿔 시작한다. SYS와 Python 수집기를 함께 업데이트해야 한다.

```powershell
python -m agent.main watch --pid 1234 --mode observe --session-id vm_v02_normal_001 --out .\runs\vm_v02_normal_001 --interval 2 --code-per-cycle 4 --thread-interval 15
```

기본 설정에서 새 스레드 검사는 약 15초 간격으로 실행된다. 3회 관찰은 첫 관찰 후 약 30초 이상이며, 처리 시간과 모듈 변동 때문에 더 늦어질 수 있다. 콜백 점검은 15초 간격, 각 응답 대기는 최소 5초다.
`kernel thread sensor unavailable`이면 구 SYS/다른 OS/누락된 API를 확인한다. 이 메시지를 무시하고 탐지가 작동한다고 해석하면 안 된다. 비교 실험에서만 `--thread-interval 0`으로 명시적으로 끌 수 있다.

## 결과 확인

정상 플레이를 먼저 5~10분 수집하고 Ctrl+C로 종료한 뒤 다음 한 줄을 실행한다.

```powershell
python .\tools\summarize_run.py --raw .\runs\vm_v02_normal_001\raw_events.jsonl
```

| 결과 | 해석 |
|---|---|
| thread_snapshots > 0, thread_scan_status의 0x00000000 | 스냅샷 요청 성공. 개별 스레드 조회 실패는 별도로 확인 |
| thread_checks_not_unique_threads > 0 | 실제 주소 대조가 수행됨. 같은 스레드 반복 검사가 포함됨 |
| callback_health의 observed > 0 | 점검 요청에서 OB pre/post 이벤트 쌍 확인 |
| system_thread_start_outside_listed_modules_persistent | 동일 스레드 시작 주소가 3회 연속 모듈 밖. 원시 스냅샷과 독립 관찰로 조사 |
| object_callback_probe_missing_persistent | 성공한 조회 핸들 점검에 대응하는 이벤트 쌍이 3회 연속 없음. 콜백/수집 경로 이상 조사 |
| coverage_gap / inconclusive | 검사 불완전. 정상이나 악성으로 단정하지 않음 |

기존 `hal.dll` 검사 실패는 이번 패치에서 해결했다고 주장하지 않는다.

## 실제 치트 평가 기준

정상 세션과 허가된 샘플 세션을 분리하고 샘플 SHA-256, 시작/정지 시각, OS 빌드, 새 SYS 해시, 기대 흔적을 별도로 기록한다.
목록 밖 시스템 스레드를 실제로 가진 샘플인지 독립적인 디버거 관찰로 확인해야 이 센서의 미탐률을 평가할 수 있다.
단순 커널 메모리 읽기·쓰기만 하는 샘플은 이 센서의 관측 범위 밖일 수 있다.
탐지 지연·미탐·오탐은 정답이 있는 실험으로만 계산한다. `cycle_ms_p95`는 수집 사이클 시간이며 CPU 사용률이 아니다.

## 검증

`python -m unittest discover -s tests -v`로 기존 정책/프로토콜, 캐시 회귀, 새 스냅샷 ABI/경계, 재사용 TID, 모듈 변동, 실패·누락 처리, 콜백 쌍 검증을 실행한다.
Python 합성 이벤트 및 이식 가능한 C 검사는 Windows 커널 런타임을 검증하지 않는다.
출시 시 수행한 정확한 결과는 `TEST_RESULTS.txt`에 있다. 다음 게이트는 WDK 빌드 → VM 정상 수집 → 실제 샘플 대조다.

## 참조

두 프로젝트의 README와 관련 소스를 검토해 검사 원리를 참고했다. 원본 코드 파일을 이 패키지에 포함하거나 그대로 이식하지 않았다.

- Darken, GPL-3.0, 검토 commit `ff82593ab1b7b2e1d0a5889bc1e9907a929abae1`:
  https://github.com/noahware/darken-anticheat
  `driver/src/krnl/threads.cpp`: 시스템 프로세스의 스레드 시작 주소 수집. 이번 구현은 전체 프로세스를 열거한 뒤 PsIsSystemThread로 판별하고 별도 시작 주소·생성 시각을 조회한다.
- ac, AGPL-3.0, 검토 commit `bd7ecfd5034893ccb71ea86a54df093c1a5b3327`:
  https://github.com/donnaskiez/ac
  `driver/thread.c`, `driver/modules.c`: 스레드 및 디스패치 주소 검사와 정상 프레임워크 예외 처리 확인. 고정 오프셋 attach 검사와 NMI/APC 코드는 이식하지 않았다.
- API 동작 참고: https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationthread
- Native 반환 구조 검토: https://github.com/winsiderss/systeminformer/blob/master/phnt/include/ntexapi.h
