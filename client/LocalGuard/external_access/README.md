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
├─ process_access/           # 위험한 게임 프로세스 핸들 탐지 (다음 단계)
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
