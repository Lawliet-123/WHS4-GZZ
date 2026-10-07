# 정상 로드 드라이버의 게임 메모리 읽기: 관측 방법 검토

## 결론

현재 KernelSentinel에는 임의의 다른 드라이버가 수행하는 게임 메모리 읽기를 직접 관측하는 센서가 없다.
검토한 공개 WDK 콜백 문서에서 모든 커널 메모리 읽기를 통보하는 범용 콜백을 확인하지 못했다.
OB/이미지 콜백에 분석 규칙을 추가해도 메모리 읽기 이벤트로 바뀌지 않는다.

이번 시험의 정답을 확인하는 현실적인 다음 단계는 **시험 드라이버의 협력적 계측 또는 별도 커널 디버거 관찰**이다.
전자는 관리하는 시험 코드의 실행을 기록하는 방법, 후자는 제한된 주소/호출을 실험실에서 관찰하는 방법이다.
어느 쪽도 이번 패치에 범용 실시간 안티치트 센서로 구현하지 않았다.

아래 비용은 설계상의 예상이며 이 VM에서 측정한 수치가 아니다.

| 방법 | 지원 기반 | 직접 확인할 수 있는 범위 | 비용·안정성·오탐 한계 | 이번 패치 |
|---|---|---|---|---|
| OB 핸들·이미지 콜백 | 공개 WDK API [1] | 핸들 요청/부여, 이미지 로드 | 이벤트 수에 비례. 읽기 실행과는 별개. 정상 장치 도구도 해당 | 기존 센서 유지, 호출 문맥 필드 추가 |
| 소유한 시험 드라이버의 TraceLogging/WPP | 문서화된 커널 ETW 계측 [2] | 계측한 복사 호출의 시작·결과·복사 바이트 | 호출당 이벤트 및 버퍼 부하, 손실·순서/시각 검증 필요. 계측 안 된 드라이버는 안 보임. 자기 보고는 공격자 독립 센서 아님 | 설계만 제공 |
| 커널 WinDbg 함수 실행 중단점 | 공식 디버깅 기능 [3] | 선택한 함수 진입 인수와 호출 스택, 반환 추적 시 상태/복사 바이트 | VM 정지/재개·타이밍 왜곡. 고빈도 시스템 함수는 과도한 중단. 진입만으로 성공 복사 확정 불가 | 절차만 제공 |
| 커널 WinDbg 데이터 중단점 | 공식 `ba` [3] | 지정한 소수 주소의 CPU 접근과 당시 실행 문맥 | x64 크기 1/2/4/8바이트, 제한된 하드웨어 슬롯. `r`은 읽기와 쓰기 모두. 정상 게임도 빈번히 접근하며 주소 수명·정렬·프로세스 문맥 관리 필요 | 절차/한계만 제공 |
| ETW CPU Profile + stack walk | WPT/xperf의 샘플링·스택 수집 [4] | 샘플 시점에 실행 중인 드라이버/함수 스택 | 짧은 복사 누락 가능, 호출 인수·대상 PID·성공 바이트를 자동 제공하지 않음. 샘플률·스택 깊이·로그량 증가 비용 | 직접 읽기 판정에 사용 안 함 |
| 하이퍼바이저의 EPT 접근 트랩 | CPU 가상화 하드웨어 [5]; 일반 WDK 콜백은 아님 | 추적 중인 물리 페이지의 접근 시도, 추가 해석 시 실행 주체 | 페이지 단위, 가상→물리 매핑 변화·페이지 공유·다중 CPU·접근 재개 처리 필요. 정상 접근도 VM exit를 유발. VMware 중첩 가상화/Hyper-V/VBS 조합 검증 필요 | 별도 연구 과제, 미구현 |

EPT 접근 위반은 접근 시도이며 그 자체가 복사 완료 증거는 아니다.
전 범위 상시 감시 비용이 낮다고 주장할 수 없고, CPU 페이지 트랩만으로 모든 DMA 경로를 포함한다고 주장할 수도 없다.
위 EPT 개발 비용/호환성 평가는 CPU 동작 문서와 현재 배포 구조에 근거한 설계 판단이며 성능 검증 결과가 아니다.

`MmCopyMemory`/커널 함수 인라인 패치나 SSDT 변경을 관측 기능으로 넣지 않았다.
Microsoft는 x64 커널 코드와 특정 구조체의 런타임 변경을 제한한다 [6].
주기적으로 비공개 KTHREAD/EPROCESS 필드를 읽어 attach 흔적을 찾는 방식도 짧은 attach를 놓칠 수 있으며,
특정 순간 attach 상태가 있었다는 사실이 실제 읽기 완료 증거는 아니므로 직접 관측으로 표시하지 않는다.

## 관리하는 시험 드라이버에 계측한다면

이 항목은 **제안 스키마이며 이번 소스에 구현되지 않았다.**
정상 테스트 드라이버의 복사 래퍼를 계측하고 운영 안티치트의 탐지 로그와 별도 파일/프로바이더로 보관한다.

| 이벤트 | 필요한 필드 | 해석 |
|---|---|---|
| `probe_read_begin` | run ID, 드라이버 빌드 식별자, 증가 request ID, 요청 PID/TID, 대상 PID/생성 시각, UTC/QPC | 읽기 요청 시작. 성공 아님 |
| `probe_copy_result` | request ID, 단계, source VA, requested bytes, copied bytes, NTSTATUS, attach 대상 신원 | 계측한 복사 호출 결과. 부분 복사와 실패를 성공과 구분 |
| `probe_read_end` | request ID, 전체 단계 결과, 좌표 출력 여부, 경과 시간 | 한 요청 완료. 중간 포인터 복사와 최종 좌표 복사를 구분 |
| 수집 품질 | 이벤트 시퀀스, ETW lost events/buffers, 시간 동기화, 활성 프로바이더/필터 | 불완전 로그를 완전한 정답으로 사용하지 않기 위한 정보 |

MmCopyMemory 문서는 반환 NTSTATUS와 실제 복사 바이트를 구분한다 [7].
attach 상태에서 불필요한 복잡한 작업을 늘리지 않도록 결과를 고정 크기 비페이지 메모리에 보관하고,
가능하면 detach 후 내보내는 구현을 별도 검토한다. 이 설계도 시험 코드 변경이므로 새 해시와 기준 성능을 남겨야 한다.
실행 코드를 소유한 시험에서는 요청/결과 건수를 비교할 수 있지만,
계측에 협력하지 않는 외부 드라이버에 동일한 관측 범위를 주장할 수 없다.

## 독립 디버거로 확인한다면

서로 다른 호스트/대상으로 연결한 커널 WinDbg와 일치하는 PDB가 있는 테스트 VM을 사용한다.
시험 드라이버 `CcpCopy` 심볼 또는 해당 함수의 실제 호출 지점을 찾아 하드웨어 실행 중단점으로 한 요청을 관찰한다.
심볼/최적화 때문에 함수가 없으면 주소를 추정하지 말고 일치하는 빌드의 디스어셈블리로 확인한다.

관찰 기록에는 다음이 함께 있어야 한다.

1. 로드 모듈/PDB/실험 바이너리 식별과 호출 스택: 실제 시험 드라이버 경로에서 진입했는지.
2. 현재 프로세스 주소 공간과 대상 게임의 PID/생성 시각: 사용자 주소가 어느 프로세스 주소인지.
3. source VA, requested bytes, 단계: 포인터 체인 중간 읽기와 좌표 읽기를 구분.
4. 반환 후 NTSTATUS와 copied bytes: 진입만으로 복사 완료라고 쓰지 않기.
5. 시각/요청 번호와 좌표 CSV 대응: 관찰한 한 요청만 확정하고 전체 102건으로 일반화하지 않기.

`ba e 1 <검증된 함수주소>`는 CPU 실행 중단점이다. 문자열의 자리표시자는 실제 주소로 바꿔야 한다.
좌표 주소가 알려진 경우 `ba r 8 /p <게임_EPROCESS_주소> <정렬된_좌표주소>`는 제한된 데이터 접근 관찰 방법이다.
`/p`에는 PID가 아닌 실제 EPROCESS 주소가 필요하고, `r`은 read/write 양쪽이다 [3].
명령어/스택을 해석해야 읽기와 쓰기 및 주체를 구분할 수 있다.
소프트웨어 코드 중단점은 코드 바이트를 바꾸어 무결성 센서에 영향을 줄 수 있으므로 그 영향도 별도 기록한다.
디버깅 실행에서 얻은 주기 p95/프레임 시간은 평상시 성능 비교에 사용하지 않는다.

## 공식 자료

1. [ObRegisterCallbacks](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-obregistercallbacks)
2. [TraceLogging for kernel-mode drivers](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/tracelogging-for-kernel-mode-drivers-and-components)
3. [WinDbg ba: Break on Access](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/ba--break-on-access-)
4. [WPT Stack Walking — Profile 예제](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/xperf/stack-walking) — 구버전 문서의 프로파일 원리 참고. 세부 옵션/제약은 설치한 WPT 버전에서 재확인.
5. [Intel SDM Volume 3C, EPT-induced VM exits](https://www.intel.com/content/dam/www/public/us/en/documents/manuals/64-ia-32-architectures-software-developer-vol-3c-part-3-manual.pdf)
6. [Driver x64 restrictions](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/driver-x64-restrictions)
7. [MmCopyMemory](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-mmcopymemory)
8. [KeStackAttachProcess](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-kestackattachprocess)
