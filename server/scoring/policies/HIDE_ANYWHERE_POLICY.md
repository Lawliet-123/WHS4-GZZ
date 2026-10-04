# Hide Anywhere 중앙 해석 정책 — A 담당

## 구현한 것

`hide_anywhere.evaluate(event, baseline)`은 원점수·기존 상태를 유지하며 관측
한계만 반환함. entity_key와 overlap_tags는 비워 둠. 현재 이벤트에 안정된
대상/사건 식별자가 없고, 중복 태그도 B와 합의 전이기 때문임.

| 현재 v9의 근거 | 점수·의미 | 중앙 해석 제한 |
|---|---|---|
| hide_value_pattern=1 | 고정 설정값 6개 패턴 일치, 3점 | 실제 숨기 행동·3회 연속 확인 증명 아님 |
| injected_module=1 | meccha.dll 이름의 모듈 관측, 보조 1점 | 파일 해시/서명·악성 주입 귀속 확인 아님 |
| viewport_hook=1 | 후보 vtable 슬롯이 메인 이미지 밖, 보조 1점 | 실제 렌더링 동작·특정 ESP 사용 확인 아님 |
| 패턴 없음 + 두 보조 근거 | 최대 2점 | 패턴 점수에 보조 점수를 다시 더하지 않음 |
| 0/0/0, raw_score=0 | 보고된 근거 없음 | 정상 최신 읽기·전체 무결성 증명 아님 |

현재 v9 생성식과 다른 구버전 점수도 원점수 그대로 보존하고 버전 차이를
표시함. 누락된 플래그를 0으로 채우거나 reason 문자열로 복원하지 않음.

## 원본 탐지기에 남은 문제 — 이 정책이 해결한 것으로 표시하지 않음

팀 main `bd66c6524b6dc8d50ded0c052191a70c8b4acf7c` 기준으로 확인함.

- `mecha_detector_v9.py:37`의 Rule은 3회 확인하지만,
  `mecha_logger.py:778`의 실행 경로는 `make_common_event()`를 직접 호출함.
  따라서 현재 3점은 패턴 1회 일치에도 나오며 중앙에서 확인 횟수를 만들지 않음.
- `mecha_logger.py:303`의 읽기 실패가 기존 캐시 값을 제거하지 않을 수 있음.
  최종 3개 플래그에 읽기 실패·누락·값의 신선도가 충분히 보존되지 않으므로,
  원시 로그를 받지 않는 중앙 정책만으로 복구할 수 없음.
- `server_bridge.py:20`의 `UUID hex:순번`은 Shared canonical UUID 검증에
  거절됨. `mecha_logger.py:664`의 기본 logger 경로도 `detection_logger.logger`임.
  이 파일에서 전송 코드를 고치거나 실패 Event를 서버 수신 성공으로 재해석하지 않음.
- 명시적 ERROR/OFFLINE/measurement_valid=false는 실패 관측으로 유지함.
  measurement_valid=true도 생산자의 보고이며 독립적인 실제 메모리 검증은 아님.

## 테스트·등록

```powershell
python -B -m unittest server.scoring.tests.test_hide_anywhere_policy -v
```

검증 결과: 새 정책 테스트 19개와 이전 정책/공통 계약까지 101개 통과함.
현재 작업 브랜치 전체 Scoring 테스트 134개도 통과함. 이는 탐지율·운영 연결 검증이 아님.

실제 v9 생성 함수와 Rule의 차이를 합성 입력으로 확인함. v9 Replay의 정상
0점/패턴 3점 Event를 원본 수정 없이 해석함. 이는 실전 탐지율·HTTPS ACK 검증은 아님.

```python
from server.scoring.policies import hide_anywhere
registry.register("hide_anywhere", hide_anywhere.evaluate)
```

등록 예시는 B 담당과 합의 후 적용할 내용임. 공통 Registry·프로필·SQLite 저장,
가중치·최종 판정·상태 만료·원본 탐지 알고리즘은 변경하지 않음.

## 2026-10-03 인수인계 범위 및 후속 합의

이 파일은 점수 정책 결정을 위한 실제 근거 조사와 읽기 전용 해석 함수임.
최종 점수 규칙을 확정하거나 원본 읽기/전송 오류를 해결한 것은 아님.
공통 Registry 등록은 인수인계대로 송희(B)가 담당함.

상태형 정상 0점 전송 방향을 B와 합의했으나, Hide Anywhere의 세 플래그 0점은
읽기 성공 여부가 부족하므로 그 자체로 정상 복귀를 확정하지 않음.
측정 유효성·읽기 실패 및 전송 경로를 원본 담당자가 수정한 뒤 재검증해야 함.
ERROR/OFFLINE을 정상화하지 않으며 heartbeat로 측정 결과를 대신하지 않음.
상태 유지·history·만료 구현은 B와 별도로 맞춰야 함.
전체 후속 결과는 [A 조사 문서 9절](../A_DETECTOR_INVENTORY.md)에 기록함.

## 2026-10-03 최신 생산자 형식 반영

이전 내용은 당시 기록으로 보존함. 현재는 `client/detectors/hide_anywhere/`의 지속적인
Rule과 확인/실패 필드가 실제 생성 경로에 연결돼 있어 단일 일치에도 3점이 나온다는
위 설명은 현재 결함이 아님. 확인 전 두 번은 핵심 0점, 세 번째부터 핵심 3점이며
읽기 실패/Pawn 변경 시 연속 횟수가 초기화되는 것을 실제 생성기로 검증함.

A 정책은 새로운 confirmed/pending reason과 count·유효성 필드를 해석하도록 수정함.
과거 single-match Replay는 원점수를 보존하되 새 3회 확인 표본으로 재해석하지 않음.
유효한 3회 패턴에는 `hide_anywhere_value_tamper`, 유효한 meccha.dll 관측에는
`hide_anywhere_injection` 후보를 부여함. 실제 숨기 성공이나 자동 점수 감산은 아님.
Shared bridge의 잘못된 custom ID/import도 현재 수정되어 조사 도구를 갱신함.
현재 생성자 → Receiver → Shared writer → Scoring HTTP 회귀 검사를 추가함.

상세 조건과 남은 B/C 연결은 [A 최신 통합 확인](../A_CURRENT_INTEGRATION.md)을 참조함.
