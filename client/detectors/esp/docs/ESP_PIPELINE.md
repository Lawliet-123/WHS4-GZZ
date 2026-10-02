# ESP 탐지 파이프라인 개발 정리

이번에 맡은 범위는 LocalGuard 중에서 외부 프로세스 접근과 게임 프로세스의 로드 모듈을 확인하는 부분이다. ESP는 게임 값을 꼭 바꾸지 않고 위치 정보만 읽어도 동작할 수 있기 때문에, 캐릭터 움직임 같은 게임 결과만 보고 찾는 방식보다는 사용자 PC에서 게임 프로세스에 누가 접근했는지 확인하는 방식으로 잡았다.

구조는 팀에서 정한 것처럼 Sensor와 Detector를 분리했다.

```text
MECCHA 게임 + Windows 상태
        │
        ├─ Sysmon ProcessAccess Sensor
        ├─ Current Process Handle Sensor
        ├─ Toolhelp Loaded Module Sensor
        └─ Window Geometry Sensor
        │
        ▼
SensorEvent (관측 사실만 기록)
        │
        ▼
EspEventDetector (규칙 적용)
        │
        ├─ EvidenceEvent → 내부 의심도 / SQLite / Dashboard
        └─ TeamEventAdapter → 팀 공통 events.jsonl
```

Sensor에서는 `score`, `suspicious`, 핵 확정 같은 값을 만들지 않는다. 예를 들어 Sysmon 센서는 소스 PID, 대상 PID, 요청 권한, 파일 경로만 정규화한다. Detector는 이 SensorEvent만 받고 `VM_READ`, `VM_WRITE`, 오버레이 형태, 새 모듈 같은 규칙을 적용한다. Detector에는 Sysmon, Win32, ctypes, pymem 같은 수집 API가 들어가지 않게 했다. 이렇게 나누면 저장해 둔 raw 로그를 나중에 ReplayAnalyzer에 다시 넣어도 같은 판정을 재현할 수 있다.

현재 폴더 구성은 아래와 같다.

```text
anti_esp/
├─ core/
│  ├─ context.py          # 세션과 정확한 게임 프로세스 인스턴스
│  ├─ events.py           # SensorEvent, SensorBatch, 팀 공통 Event
│  ├─ interfaces.py       # Sensor / Detector 인터페이스
│  └─ session.py          # manifest, events, raw 로그 저장
├─ sensors/
│  ├─ process_access.py   # Sysmon Event ID 10 수집
│  ├─ handle_sensor.py    # 현재 열린 외부 프로세스 핸들 수집
│  ├─ module_sensor.py    # Toolhelp 모듈 스냅샷
│  ├─ module_events.py    # 모듈 추가·제거·변경 이벤트
│  ├─ window_overlap.py   # 창 위치와 스타일 수집
│  ├─ signature.py        # WinVerifyTrust
│  ├─ file_identity.py    # SHA-256 + 서명 결과 결합
│  └─ identity.py         # 선택형 PC 가명 식별자
├─ detectors/
│  └─ esp_detector.py     # 순수 규칙 판정
├─ pipeline.py            # Sensor → Detector → 저장
├─ team_format.py         # 팀 공통 Event 변환
├─ scoring.py             # 내부 0~100 의심도
└─ controller.py          # 실행 주기와 전체 연결
```

외부 접근 수집은 Sysmon Event ID 10을 사용한다. 이벤트의 대상 PID가 지금 실행 중인 게임 PID와 같은지 먼저 확인하고, 게임 프로세스 생성 시각보다 오래된 이벤트는 제외한다. PID는 종료 후 재사용될 수 있기 때문에 PID만 저장하지 않고 `game-process:<pid>:<created_at_ms>` 형태의 subject ID를 만든다.

실제 SensorEvent 생성 부분은 다음과 같다.

```python
return SensorEvent(
    session_id=context.session_id,
    sensor_id="sysmon_process_access",
    event_type="process_access",
    subject_id=target.subject_id,
    timestamp_ms=int(round(timestamp * 1000.0)),
    payload={
        "source_pid": record.source_process_id,
        "source_image": record.source_image,
        "target_pid": record.target_process_id,
        "target_image": record.target_image,
        "granted_access": access,
        "granted_access_hex": f"0x{access:08X}",
        "access_labels": list(record.access_labels),
        "call_trace": record.call_trace,
    },
)
```

여기까지는 접근이 위험한지 판단하지 않는다. Detector에서 권한 조합을 보고 해석한다.

```python
if labels & {"CREATE_THREAD", "VM_OPERATION", "VM_WRITE"}:
    category = "process_tamper"
elif "VM_READ" in labels:
    category = "memory_read"
elif "DUP_HANDLE" in labels:
    category = "handle_duplicate"
else:
    return ()
```

`VM_READ`는 ESP처럼 게임 정보를 읽을 수 있는 직접 신호로 raw score 2를 준다. `VM_WRITE`, `VM_OPERATION`, `CREATE_THREAD`가 포함되면 읽기보다 위험한 접근으로 보고 raw score 3을 준다. `DUP_HANDLE`은 다른 프로세스의 핸들을 전달받을 가능성이 있지만 이것만으로 핵이라고 할 수 없어서 1점 보조 신호로 둔다.

Sysmon은 접근이 일어난 시점의 기록이고 현재도 핸들이 살아 있는지는 직접 보여 주지 않는다. 이 부분은 `NtQuerySystemInformation(SystemExtendedHandleInformation)`으로 시스템 핸들 목록을 제한된 크기로 읽고, 외부 프로세스 핸들을 조회 전용으로 복제한 뒤 `GetProcessId`로 실제 대상이 게임 PID인지 확인하는 센서를 추가했다. 첫 성공 조회는 기준선으로만 잡고 새 핸들은 바로 기록하며, 처음부터 있던 핸들이 계속 유지되면 쿨다운 뒤 주기 이벤트로 기록한다. 복제한 핸들과 조회용 프로세스 핸들은 모두 닫는다.

모듈 감시는 `CreateToolhelp32Snapshot`과 `Module32FirstW/Module32NextW`로 게임 프로세스의 모듈 경로, 베이스 주소, 이미지 크기를 읽는다. 첫 번째 스냅샷을 그대로 경고하면 게임이 원래 불러온 DLL 전체가 잡히므로 첫 성공 결과는 기준선으로 사용한다. 각 기준선 모듈은 `module_present` 사실 이벤트로 만들고 파일 신원과 서명도 확인하지만, 존재 자체와 미서명·신뢰 거부 결과에는 점수를 주지 않는다. 정확한 알려진 악성 해시 일치만 예외로 판정한다. 세션 텔레메트리를 켠 실행에서만 이 raw 이벤트가 디스크에 저장된다. 그 다음 스냅샷부터 경로가 새로 생긴 모듈, 사라진 모듈, 같은 경로인데 주소나 크기가 바뀐 모듈을 나눠서 기록한다.

```python
previous = self._previous_by_pid.get(snapshot.pid)
self._previous_by_pid[snapshot.pid] = snapshot
if previous is None:
    return ModuleDiff(
        pid=snapshot.pid,
        previous_captured_at=None,
        current_captured_at=snapshot.captured_at,
        baseline_created=True,
    )
return diff_module_snapshots(previous, snapshot)
```

기준선과 새 모듈 경로는 SHA-256과 Authenticode 서명 결과를 같이 수집한다. 서명 검사는 Windows `WinVerifyTrust` 반환값을 `trusted`, `unsigned`, `rejected`, `error`, `unavailable`로 정규화한다. 반환값 0만 신뢰 성공이고, 알려진 서명·인증서·정책 거부 코드만 `rejected`로 분류한다. 공급자 미등록처럼 판단 자체가 불가능한 코드는 `error`로 남겨 점수화하지 않는다. 정상 개인 빌드도 미서명일 수 있고 서명된 파일도 악용될 수 있어서, `새 DLL`, `unsigned`, `rejected` 하나만으로 탐지 확정은 하지 않는다. 내부 점수에서도 모듈 신호는 낮은 보조 범주로 제한했다.

오버레이 센서는 외부 창의 위치와 확장 스타일만 수집한다. Detector에서 게임 창의 55% 이상을 덮고 `LAYERED`, `TRANSPARENT`, `TOPMOST` 중 두 개 이상이 같이 있을 때만 보조 근거 1점으로 바꾼다. Discord, Steam, GPU 오버레이처럼 정상 프로그램도 비슷한 모양을 가질 수 있기 때문에 단독 고위험 신호로 사용하지 않는다.

팀 공통 결과 포맷은 전달받은 필드 그대로 맞췄다.

```json
{
  "session_id": "esp_001",
  "player_id": "player_042",
  "module": "esp",
  "timestamp_ms": 7000,
  "evidence": {
    "source_pid": 4242,
    "target_pid": 777,
    "granted_access_hex": "0x00000010",
    "access_labels": ["VM_READ"]
  },
  "reasons": [
    "external process requested permission to read game memory"
  ],
  "raw_score": 2
}
```

여기서 `timestamp_ms`는 시스템 Unix 시간이 아니라 테스트 세션을 시작한 뒤 지난 시간이다. 예를 들어 30초에 ESP를 켰으면 그 이후 결과가 30000ms 근처에 나와야 ReplayAnalyzer에서 ON/OFF 구간과 바로 비교할 수 있다. 원래 센서 시간은 raw 로그에 따로 보존한다.

현재 ESP raw score 기준은 아래처럼 시작했다.

| 신호 | raw_score | 의미 |
| --- | ---: | --- |
| VM_WRITE / VM_OPERATION / CREATE_THREAD | 3 | 게임 메모리 변경 또는 원격 실행에 사용할 수 있는 직접 권한 |
| VM_READ | 2 | 외부 프로세스가 게임 메모리를 읽을 수 있는 권한 |
| DUP_HANDLE | 1 | 핸들 전달 가능성이 있는 보조 신호 |
| 오버레이 형태 | 1 | 화면 표시형 ESP와 비슷한 창 형태지만 정상 오버레이 가능 |
| 새 모듈 / 미서명·서명 오류 | 1 | DLL 변화 보조 신호, 단독 확정 금지 |
| 알려진 악성 해시 | 3 | Detector 입력은 지원하지만 현재 지완 담당 센서에서는 블랙리스트 매칭을 만들지 않음 |

이 점수는 서버의 최종 risk score가 아니다. 정상 로그와 승인된 ESP 실행 로그를 ReplayAnalyzer에서 비교한 뒤 가중치와 임계치를 다시 정하기 위한 모듈별 원시 점수다. 내부 대시보드의 0~100 의심도와 팀 `raw_score`도 서로 다른 값이므로 직접 더하지 않는다.

로그는 세션별로 다음과 같이 저장한다.

```text
data/sessions/esp_001/
├─ manifest.json
├─ events.jsonl
└─ raw/
   ├─ sysmon_process_access.jsonl
   ├─ current_process_handles.jsonl
   ├─ loaded_modules.jsonl
   └─ window_overlap.jsonl
```

`raw/`에는 SensorEvent를 그대로 넣고, `events.jsonl`에는 Detector가 근거를 만든 경우에만 팀 공통 Event를 넣는다. `manifest.json`에는 세션 이름, player ID와 별도로 기록할 테스트 종류, 핵 ON/OFF 시간, 파일별 레코드 수를 남긴다.

SQLite 근거와 공통 JSONL 사이에는 outbox를 뒀다. 먼저 근거와 전송할 JSON을 하나의 SQLite 트랜잭션으로 확정하고, 커밋이 끝난 뒤 `events.jsonl`에 쓴다. 파일 쓰기가 실패하면 outbox는 미전달 상태로 남고, 다시 보낼 때 같은 idempotency key를 사용해 중복 줄이 생기지 않게 했다. 수집기나 실행 본문에서 치명적 오류가 나면 manifest 상태는 `completed`가 아니라 `failed`와 오류 원인으로 남는다.

실행 예시는 아래와 같다.

```powershell
# 정상 플레이 로그
python .\run.py --headless `
  --session-id normal_001 `
  --player-id player_042 `
  --scenario normal

# 승인된 ESP 테스트 로그: 30초 ON, 70초 OFF
python .\run.py --headless `
  --session-id esp_001 `
  --player-id player_042 `
  --scenario esp `
  --cheat-on-ms 30000 `
  --cheat-off-ms 70000
```

HWID는 원문 디스크·보드 번호를 로그로 보내는 방식으로 만들지 않았다. 기본값은 비활성화이고, 동의를 받아 켰을 때만 Windows 설치 식별자들을 메모리에서 정규화한 다음 HMAC-SHA-256 결과만 남긴다. 서버 pepper가 없으면 설치 단위 가명이라 재설치 후 바뀔 수 있고, pepper가 있어도 하드웨어나 OS 정보가 달라질 수 있으므로 불변 HWID라고 표현하지 않는다.

현재 자동 테스트에서는 센서 오류와 정상 빈 결과 구분, PID 재사용, 첫 모듈 기준선, DLL 추가·변경, 서명 결과, 허용목록, 팀 JSON 필드, 경과시간 변환, SQLite 중복 억제, 창 겹침 규칙까지 확인한다. 실제 Windows Toolhelp로 현재 Python 프로세스 모듈 열거와 DLL 변화 실측을 했고, `WinVerifyTrust`로 Python 실행 파일이 반환 코드 0인 것도 확인했다.

아직 완료됐다고 적으면 안 되는 부분도 있다. 실제 게임을 켠 상태에서 정상 세션 1개와 승인된 ESP 세션 1개를 같은 조건으로 수집하는 검증은 별도로 해야 한다. Sysmon Event ID 10은 프로세스를 열면서 요청한 권한 기록이지 모든 `ReadProcessMemory` 호출 내역은 아니다. 현재 핸들 센서도 보호 프로세스처럼 핸들 복제가 거부된 대상은 놓칠 수 있다. Toolhelp 목록에 등록되지 않는 수동 매핑 DLL, 커널 드라이버, DMA, 안티치트 자체 변조도 이 사용자 모드 센서만으로 완전히 잡을 수 없다. 따라서 현재 결과는 자동 밴이 아니라 사람이 검토하고 ReplayAnalyzer 임계치를 정하기 위한 근거로 사용한다.

실제 제출 전에는 다음 두 세션을 만들어 전달하면 된다.

```text
normal_001
→ 정상 플레이

esp_001
→ ESP
→ 30000ms ON
→ 70000ms OFF
```

각 세션 폴더 전체를 압축하면 raw 로그, 공통 Event, 테스트 메타데이터가 같이 들어가므로 ReplayAnalyzer와 Dashboard에서 바로 비교할 수 있다.

같은 `session_id`를 두 번 사용하면 서로 다른 실행 로그가 한 파일에 섞여 탐지율과 탐지시간 계산이 틀어질 수 있다. 그래서 이미 존재하는 세션 이름은 이어 쓰지 않고 실행을 중단하며, 다시 측정할 때는 `normal_002`, `esp_002`처럼 새 이름을 사용한다.
