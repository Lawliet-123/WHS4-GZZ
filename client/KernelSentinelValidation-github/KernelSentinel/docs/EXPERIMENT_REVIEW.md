# KernelSentinel v0.2.1 커널 좌표 읽기 시험 재검토

대상: `mecha_kernel_cheat_001`, 게임 PID 2376, 클라이언트 PID 6056.
시간은 2026-09-26 UTC. v0.2.2 오프라인 분석기로 기존 자료를 다시 분석했다.

**시험 도구의 좌표 읽기는 성공했다고 보고됐지만, KernelSentinel이 커널 읽기 동작을 탐지했다는 근거는 없다.**
로드·핸들·읽기 보고는 아래와 같이 다른 사실이다. 기록 손실 0과 코드 변화 없음은 이 구분을 바꾸지 않는다.

## 1. 세 사실을 분리한 결과

| 사실 | 확인한 증거 | 판정 범위 |
|---|---|---|
| 드라이버 로드 | 17:06:21.1271997 `kernel_image`, 시퀀스 2689, 세대 4; 뒤이은 모듈 목록 및 파일 해시 | 정상 로더 경로로 드라이버 이미지가 관측됨. 메모리 읽기를 입증하지 않음 |
| 클라이언트의 게임 핸들 접근 | PID 6056→2376, `0x1000`과 `0x1FFFFF` 요청/성공 부여 두 작업 | 권한 부여를 관측함. 그 핸들을 사용한 읽기는 입증하지 않음 |
| 실제 읽기 동작 | 외부 CSV `complete/status=0` 102행, 서로 다른 좌표 31개, 연속 값 변화 30회; 사용자의 이동 확인 | 소스/시험 파일과 함께 읽기 성공의 시험 근거로 취급. **Sentinel 자체의 직접 관측 아님** |

`common_events.jsonl`에는 PID 6056의 `sensitive_handle_rights_granted/raw_score=1`이 1건이다.
`0x1000` 작업에는 같은 경고가 없으므로 공통 이벤트 수 1과 원시 핸들 작업 수 2는 모순이 아니다.
`raw_repeated_findings={}`는 이전 요약기의 일부 커널 규칙 집계가 비었다는 뜻이며, 모든 경고가 없었다는 뜻도 아니다.

## 2. 시간순 연결과 근거

원시 JSONL 행 번호는 빈 줄을 포함해 1부터 센다. 정확한 입력 해시는 아래에 있다.

| UTC | 원시 행 | 사건 | 연결 근거/한계 |
|---|---:|---|---|
| 17:06:21.1271997 | 2462 | ChameleonKernelProbe.sys 이미지 로드 | 로드 알림 자체 |
| 17:06:21.2110000 | 2472 | 드라이버 목록에 같은 base/size | 같은 주소·크기, 로드 뒤 수집. 언로드/주소 재사용을 완전히 식별하지 못함 |
| 17:06:21.3130000 | 2476 | 파일 SHA-256 분석 | `source_sequence=2689/generation=4`로 로드 이벤트와 연결 |
| 17:06:21.3130000 | 2477 | 같은 파일의 다른 경로 표기 해시 | 목록에서 나온 파일 분석. 독립적인 두 번째 로드로 세지 않음 |
| 17:13:38.2272109 | 8417 | 클라이언트 PID 6056 생성 | 생성 시각 134349164182265107과 경로. 앞선 로드와는 시간상 후보 연결뿐 |
| 17:13:38.2326628~2326677 | 8418~8419 | `0x1000` 요청/부여 | 세대 4, 작업 3215, actor/target 일치 |
| 17:13:38.2328365~2328562 | 8420~8421 | `0x1FFFFF` 요청/부여 | 세대 4, 작업 3216, actor/target 일치 |
| 17:13:38.265~17:14:34.748 | 외부 CSV | 좌표 성공 102회 | 대상 PID/생성 시각(밀리초 정밀도)과 세션 구간 일치. CSV에 클라이언트 PID는 없어 시간 중첩만으로 호출자를 확정하지 않음 |
| 17:14:35.0832264 | 9459 | PID 6056 종료 | 관측된 동일 생성 시각 |

앞서 같은 이름으로 시작했다 바로 종료한 PID 9052, 2500, 1128, 6728도 별도 프로세스 수명으로 유지한다.
분석 결과 전체와 기계 판독용 연결은 원본 보관본의 `KernelSentinel/reports/current_case/`에 있다. 해당 실행 기록은 공개용 소스 저장소에서 제외했다.

로드→프로세스 생성의 시간 근접만으로 그 프로세스가 해당 드라이버에 IOCTL을 보냈다고 확정하지 않는다.
정상 설치 도구·장치 관리 프로그램·오버레이·디버거에서도 이미지 로드와 프로세스/핸들 이벤트가 함께 나타날 수 있다.
과도한 권한 요청은 검토 근거지만 그 자체로 치트 확정은 아니다. 모든 상관 연결의 추가 점수는 0이다.

파일 해시는 분석 시점의 **디스크 바이트** 신원이다. 실제 로드 메모리 전체와 동일하다는 보증이 아니다.
관측된 SHA-256은 `590474f21d889f295335e758db47d4b27e9c4d07c0484c7d6fe9e4f4b4b8a2cd`이며,
별도 제공된 VM `.sys` 파일과 일치한다. ZIP 내부 `.sys`는 전체 파일 해시가 다르므로 같은 바이너리라고 간주하지 않았다.
검토한 두 파일의 실행 섹션은 일치했지만 이것도 빌드/서명 메타데이터까지 같다는 뜻은 아니다.
VM에서 실행한 클라이언트 EXE의 해시는 이번 Sentinel 로그로 확인되지 않았다.

## 3. PID 6056의 0x1FFFFF 원인: 미확인

제공된 `client/ChameleonCoord.cpp`의 `Identify()`는 직접 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid)`를 호출한다.
패키지 EXE 정적 분석에서도 직접 OpenProcess 호출 앞의 요청값 `0x1000`을 확인했다.
그 뒤 소스에는 이미지 이름/생성 시각 조회와 `CreateToolhelp32Snapshot(0x18, pid)`, `Module32FirstW`가 있다.

첫 번째 핸들 post에서 두 번째 pre까지 약 168.8µs로 가까우므로 같은 초기화 경로에서 발생했을 가능성은 조사할 가치가 있다.
그러나 현재 로그에는 호출자 TID, API 구간, native 스택이 없다. `thread_id=0`을 호출자 TID로 읽어서는 안 된다.
Toolhelp 내부에서 발생했다고 확정할 근거가 없고, Microsoft의 공개 API 계약도 내부 요청 권한을 `0x1FFFFF`로 보장하지 않는다.
따라서 원인을 **미확인**으로 유지한다. 커널 읽기 드라이버의 메모리 접근 때문이라는 설명도 입증되지 않았다.

v0.2.2는 actor TID/생성 시각을 추가하고 별도 진단 클라이언트가 메타데이터 API 구간을 기록한다.
같은 신원·시간 구간은 후보를 좁힐 뿐이다. 재시험 native `NtOpenProcess` 진입 스택에서 대상 게임 PID와 DesiredAccess를 같이 확인해야 호출 근거가 된다.
새 실행에서 원인을 알아도 과거 실행의 스택을 복구한 것은 아니므로 기존 시험의 원인을 소급 확정하지 않는다.

## 4. 기존 센서가 읽기를 관측하지 못한 이유

검토한 시험 소스는 IOCTL 요청을 처리하는 기존 실행 흐름에서 대상 프로세스를 확인하고,
`KeStackAttachProcess` 후 `MmCopyMemory`로 좌표를 복사한 뒤 detach한다.
새 시스템 스레드 생성이나 커널 코드 변조가 이 읽기 경로의 전제는 아니다.
그 경로의 읽기에 매번 게임 프로세스 핸들을 여는 코드도 없다.
따라서 시스템 스레드 시작 주소 검사·코드 무결성·OB 핸들 콜백만으로 모든 읽기가 나타날 것으로 기대하면 안 된다.
CSV의 `attached=0`은 이 도구의 게임 객체 AttachParent 관련 값이며 커널 attach 미실행을 의미하지 않는다.

지원되는 방법과 비용, 관측 범위는 [OBSERVATION_METHODS.md](../docs/OBSERVATION_METHODS.md)에 정리했다.
이번 구현의 결론은 **정상 로드된 다른 드라이버의 게임 메모리 직접 읽기: 관측 공백**이다.

## 5. 수집 품질과 실제 검증 범위

관측된 큐 손실 최대 0, 명시적 coverage_gap 0. 시스템 스레드 스냅샷 41회, 누적 검사 8,477회(고유 스레드 수 아님),
쿼리 실패 0, 모듈 밖 시작 주소 관측 0, callback_health observed 45회다.
시험 드라이버 코드 검사는 최초 기준 설정 1회와 동일 판정 5회였다.
전체 코드 검사 1,300회 중 7회는 `hal.dll/status=0xC0000225`로 불완전했다. 전체 코드가 항상 검증됐다고 확대하면 안 된다.
원래 시험의 수집 주기 p95는 77.071ms이며 새 패치 성능 수치가 아니다.

시험 성공 기준은 [RETEST.md](../docs/RETEST.md) 참고. 이 버전의 로그만으로 실제 읽기 탐지가 성공했다고 선언하지 않는다.

## 입력 식별

| 제공 파일 | SHA-256 |
|---|---|
| 02-raw_events.jsonl | 443c6795eebecd3e12d4aa4f2c2062939e01e1fd90b8df601e71feb168483381 |
| 01-common_events.jsonl | 3a0b8cf871714cc47b0e10920c7c6f79aac7555abe43b02322889e415532642a |
| 01-coord_read_log.csv | 246adb3bf29afa5d9ec62c1125468500ed39f9118031f38fdc873b9f562699fd |
| 01-ChameleonKernelProbe-v3-package.zip | 1869846f775a156a196c0789a662f385ade923ac3c09795a743a83fcfc9dc5d5 |
| 01-ChameleonKernelProbe.sys | 590474f21d889f295335e758db47d4b27e9c4d07c0484c7d6fe9e4f4b4b8a2cd |

## 공개 API 근거

- [CreateToolhelp32Snapshot](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot)
- [MmCopyMemory](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/nf-ntddk-mmcopymemory)
- [ObRegisterCallbacks](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-obregistercallbacks)
