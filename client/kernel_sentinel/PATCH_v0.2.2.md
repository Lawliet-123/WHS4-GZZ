# v0.2.2: 관측 사실과 판정의 경계

정상 로드된 좌표 읽기 드라이버를 실행했으나 KernelSentinel은 읽기 자체를 관측하지 못했다.
이번 수정은 그 공백을 숨기지 않고 보고하며, 예상 밖 핸들의 원인을 재시험에서 조사할 근거를 추가한다.

## 구현한 변경

| 파일 | 변경과 결과 |
|---|---|
| `tools/correlate_evidence.py` | 드라이버 로드, 파일 분석, 모듈 목록, 클라이언트 생성/종료, 게임 핸들 pre/post, 외부 CSV를 UTC 시간순으로 연결. 원시 행 번호와 연결 이유·한계 유지. 연결 추가 점수 0 |
| `tools/summarize_run.py` | 드라이버 로드 수, 핸들 결과, 직접 읽기 센서 미구현을 별도 표시. 기존 `raw_repeated_findings`가 전체 공통 이벤트 목록이 아니었음을 명시 |
| `driver/KernelSentinel.c`, `driver/protocol.h` | 핸들 pre에서 요청 실행 문맥의 TID와 프로세스 생성 시각을 기록하고 post로 유지. 호출 스택·원인 드라이버 정보는 아님 |
| `agent/protocol.py` | 새 플래그 `0x100`이 있을 때 `actor_tid`, `actor_create_time`, `caller_identity_scope` 해석. 플래그 없는 기존 로그에 필드를 만들어 넣지 않음 |
| `agent/main.py` | 세션 `agent_version=0.2.2`, `direct_kernel_memory_read_sensor=not_implemented` |
| `tools/instrument_client.py`, `client_call_trace.h` | 제공된 v3 클라이언트의 메타데이터 API 5개 begin/end를 기록하는 진단 복사본 생성. 원본 수정 없음. PID/생성 시각/TID/UTC/QPC/요청 권한/반환값 기록 |
| `tests/test_evidence.py` | PID 재사용, 잘못된 세대/시퀀스, 외부 로그 신원 불일치, 불완전 핸들/API 쌍, 구형 프로토콜, 오판정 방지 검증 |

이벤트 ABI는 616바이트 그대로이며, 핸들 이벤트에서 미사용이던 `ImageBase/ImageSize`를 새 플래그로 구분해 사용한다.
`thread_id`는 대상 스레드 ID이므로 호출자 TID로 해석하지 않는다.
기존 드라이버와 새 수집기도 기존 이벤트를 해석할 수 있지만 새 문맥 정보는 **드라이버를 재빌드·재로드해야** 생긴다.
업데이트 확인은 에이전트 버전만 보지 말고 실제 핸들 이벤트의 `flags & 0x100`, `actor_tid`, `actor_create_time`으로 한다.

실행 문맥 PID/TID는 커널 attach 등의 영향을 함께 고려해야 하며 인과관계나 특정 드라이버 신원 자체가 아니다.
외부 API 로그가 같은 PID·생성 시각·TID·대상과 시간 구간으로 맞아도 `api_interval_candidate`까지만 출력한다.
네이티브 스택의 수동 검토 결과를 이 도구가 자동으로 확정 판정하는 기능은 없다.

## 실행

Windows에서 기존 성공한 WDK 구성으로 빌드·재로드한 뒤 [재시험 절차](docs/RETEST.md)를 따른다.
이미 가진 v0.2.1 로그는 재실행 없이 아래 분석기로 읽을 수 있다.

```powershell
python .\tools\correlate_evidence.py --raw .\runs\mecha_kernel_cheat_001\raw_events.jsonl --coord .\coord_read_log.csv --driver-name ChameleonKernelProbe.sys --client-name ChameleonCoord.exe --out .\analysis\case_001
```

`--out`은 새 폴더여야 한다. `report.md`, `evidence.json`, `timeline.jsonl`을 생성한다.
`--window-seconds` 기본 900초는 로드→클라이언트 생성 후보 연결 범위이며 악성 판정 임계치가 아니다.
`--calls`에 진단 클라이언트 JSONL을 주면 API 구간 후보를 추가한다.
`--kernel-only` 로그는 게임 신원이 없어 이 분석기에서 거부한다. 실제 게임 핸들 분석은 게임 PID 모드가 필요하다.

## 완료/미완료 범위

Linux 오프라인 회귀 테스트 54개 및 제공된 실제 로그 재분석을 수행했다.
새 Windows 드라이버/진단 클라이언트 빌드, VM 실행, Driver Verifier, 프레임 시간·CPU 측정은 수행하지 못했다.
TraceLogging 계측, 커널 디버거 관찰, EPT 모니터는 검토/절차만 포함하며 자동 센서로 구현하지 않았다.
이번 패키지는 읽기 탐지 성능 개선을 입증한 버전으로 부르지 않는다.
