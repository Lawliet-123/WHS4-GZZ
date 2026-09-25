# LocalGuard external_access

외부 프로세스 접근 분석과 게임 내부 모듈 무결성 탐지기가 함께 쓰는 공통 기반이다.
현재는 중앙 서버 전송 없이 탐지 결과를 로컬 JSONL에만 기록한다.

```text
external_access/
├─ common/
│  ├─ models.py              # EXE/DLL 검사 결과와 게임 프로세스 식별자
│  ├─ detection_result.py    # 합의한 탐지 JSON 생성·검증
│  ├─ jsonl_writer.py        # 결과 한 건을 로컬 JSONL에 추가
│  ├─ process_locator.py     # 게임 PID 탐색·재실행 판별
│  ├─ artifact_inspector.py  # SHA-256·Authenticode·게시자 조회
│  └─ artifact_cache.py      # 파일이 안 바뀌었으면 검사 결과 재사용
├─ process_access/           # 위험한 게임 프로세스 핸들 관찰·점수화·JSONL 기록
├─ module_integrity/         # DLL 기준선·변화 탐지 (다음 단계)
└─ tests/
```

## 현재 결과 형식

```python
{
    "session_id": "session_20260915_001",
    "player_id": "player_042",
    "module": "external_access",
    "timestamp_ms": 507000,
    "evidence": {"access_mask": "PROCESS_VM_WRITE"},
    "reasons": ["Untrusted process has VM_WRITE access to the game"],
    "raw_score": 3,
}
```

`common/detection_result.py`가 위 형식을 검증하고,
`common/jsonl_writer.py`가 한 결과를 한 줄의 JSON으로 기록한다.

## 테스트

저장소 루트에서 실행한다.

```powershell
py -3 -m unittest discover -s client/LocalGuard/external_access/tests -t . -v
```

단위 테스트는 SHA-256, 캐시 무효화, 게임 PID 재시작 식별, JSONL 출력,
위험 권한 점수화, handle 수집 필터, 실행기 연결을 검증한다. 실제 게임 대상
handle 수집은 실환경에서 별도로 확인한다.

## 외부 프로세스 접근 분석 실행

게임을 실행한 뒤, 저장소 루트의 관리자 PowerShell에서 한 번만 관찰하려면
아래처럼 실행한다.

```powershell
py -3 -m client.LocalGuard.external_access.process_access.runner --game-exe PenguinHotel-Win64-Shipping.exe --session-id local_test_001 --player-id player_042 --once
```

반복 관찰은 `--once`를 빼고 실행한다. 위험 권한이 발견됐을 때만 기본 경로
`logs/external_access.jsonl`에 결과 한 줄이 기록된다. 이 모듈은 차단·종료·전송을
하지 않는다.

검토가 끝난 정상 프로세스는 `process_access/allowlist.json`에 실행 파일 이름과
SHA-256을 함께 등록한다. 이름만으로 예외 처리하거나 첫 실행 결과를 자동 등록하지
않는다. 게임 또는 Windows 업데이트로 해시가 바뀌면 다시 정상 여부를 확인한 뒤
목록을 갱신한다.

Windows 핵심 프로세스처럼 강한 예외가 필요한 항목은 이름과 SHA-256뿐 아니라
`executable_path`, `signature_status`, `publisher_contains`도 함께 지정한다. 네 조건을
모두 만족할 때만 정상 처리하므로, 같은 이름으로 위장하거나 서명이 깨진 파일은
예외 처리되지 않는다. Windows kernel `System`(PID 4)은 사용자 영역 실행 파일이
아니므로 handle 수집 단계에서 제외한다.
