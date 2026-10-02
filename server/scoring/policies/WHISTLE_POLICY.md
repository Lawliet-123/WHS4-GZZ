# Whistle 중앙 해석 정책 — A 담당 1차 구현

> 최신 A·B 합의는 아래 후속 절과 [전체 조사 문서 9절](../A_DETECTOR_INVENTORY.md)을 참조함.
> 양수-only 관련 설명은 구현 당시 확인한 전송 경로임. 정상 0점 전송 합의와 코드 반영 완료를 구분함.

## 구현 범위

`whistle.py`의 `evaluate(event, baseline)`를 구현함. Shared 공통 7필드 입력을
`PolicyRegistry`가 검증하고 `inspect_event()` 결과를 만든 뒤, 정책은
`PolicyAnnotations`만 반환함. 원시 점수·원본 이벤트·공통 프로필·SQLite·최종
판정을 변경하지 않음. 클라이언트 원본 탐지기도 수정하지 않음.

| 입력 module | 관측 의미 | 처리함 |
|---|---|---|
| whistle | 스캔 시점의 ExecFunction/vtable/사운드 교체 흔적 | 알려진 reason을 해석하고 반복 점수의 신규 사건 가산을 경고함 |
| whistle_rpc | 후크 로그의 호출 위반 코드별 점수 | 구간/전체 로그를 구분하며 코드별 점수에 위반 횟수를 다시 곱하지 않음 |

## 상태·식별·전송 처리

- `baseline.state=MEASUREMENT_UNAVAILABLE`인 ERROR/OFFLINE/측정 무효는 정상
  또는 이전 위험 해제로 해석하지 않음. 양수 근거가 함께 있어도 실패 상태와
  원점수는 보존하고 entity/tag를 생성하지 않음.
- NORMAL 0점은 실제 관측 범위의 위반 없음일 뿐 전체 무결성 보장이 아님.
  상태가 없는 0점은 NORMAL로 보완하지 않음.
- 실제 현 클라이언트는 로컬 0점을 기록하지만 중앙 전송은 양수 필터를 사용함.
  미전송을 정상·검사 성공으로 치환하지 않음. 자동 초기화·만료 수치는 미정임.
- 양수 `whistle`의 `evidence.meta.target_pid`가 유효한 DWORD 정수이면
  `game_pid:<PID>`를 반환함. 이는 **검사 대상 게임 프로세스 범위**임. 외부 핵
  프로세스·계정·개별 함수 사건 ID가 아님. PID 재사용 주의를 함께 반환함.
  주소가 포함된 자유 문자열을 파싱해 사건 키를 만들지 않음.
- RPC는 안정된 호출자·사건 ID가 없어 entity_key를 비워 둠. 로그 파일 경로,
  회차나 누적 호출 수를 키로 사용하지 않음. `meta.mode=window`에서 새 로그
  구간이라는 의미는 보존하되 Godmode의 사건 증분 계약으로 편입하지 않음.
- 구간 `window_calls=0`과 실제 호출을 관측한 정상 결과를 구분함. 서버 정책
  자체가 클라이언트 후크 생존이나 캡처 내용의 진실성을 검증한 것은 아님.
- **overlap_tags는 합의 전 빈 tuple임.** reason별 해석은 구현했지만 공통 태그를
  임의로 확정하지 않음. 따라서 현재 상관 후보 생성에 참여하지 않음.
- 이 주석은 기존 SQLite 최신 상태 키를 변경하거나 과거 사건 이력을 생성하지 않음.

## 연결 방법 — B가 공통 Registry에서 반영할 부분

```python
from server.scoring.policies import whistle

registry.register("whistle", whistle.evaluate)
registry.register("whistle_rpc", whistle.evaluate)
```

현재 테스트는 독립 Registry에 두 이름을 등록하여 확인함. B 관리 영역인
공통 `registry.py`, `policy.py`, `storage.py`, `main.py`, `contract.py`는
수정하지 않음. 기본 Registry·실제 서버 실행에 자동 등록된 것으로 표현하지 않음.

## 자료·테스트 구분

- 저장소 `ReplayAnalyzer/replay-data/`의 RPC 40건은 ERROR임.
- 별도 `client/detectors/whistle-spoofing/measurements/`에 정상 0점
  (`rpc_clean_001.jsonl:4`)과 연타 위반 40점 (`rpc_pi_002.jsonl:1`) 기록이 있음.
- 캡처는 옛 `score` 형식이며 테스트에서만 7필드로 포장함. player_id가 없는
  캡처에는 테스트용 ID를 명시적으로 넣음. 원본을 수정하거나 실전 중앙 전송이
  성공한 것처럼 해석하지 않음. 정상 파일 전체를 정상 라벨로 사용하지 않음.
- 정책 단위 테스트는 원본·점수 보존, 실패 상태, 부분 검사, PID 유효성, RPC
  구간/전체 로그, 호출 0건/관측 호출, 횟수 재가산 방지, 미분류 근거, 반복
  평가, 7필드 검증 및 캡처 2행 해석을 검사함.

```powershell
python -B -m unittest server.scoring.tests.test_whistle_policy -v
```

### 2026-10-03 검증 결과

- 새 Whistle 테스트 25개와 공통 계약 테스트 11개: **36개 통과**.
- 팀 main `bd66c6524b6dc8d50ded0c052191a70c8b4acf7c`의 Scoring 코드와 함께
  실행한 기존 테스트 81개 + 새 Whistle 테스트 25개: **106개 통과**.
  최신 main을 포함한 별도 검토 체크아웃에서 공통 코드를 불러오고, 새 정책과
  테스트만 모듈 검색 경로에 추가하여 검사함. 브랜치 병합이나 기본 Registry
  등록을 완료했다는 의미는 아님.
- 전체 테스트의 첫 실행은 임시 SQLite 폴더 접근 권한으로 실패함. 동일한
  로컬 테스트를 권한 제한 없이 재실행한 결과 모두 통과함. 코드 오류로
  임시 DB 실패를 우회하거나 공통 저장 코드를 수정하지 않음.
- 이는 입력 해석과 기존 코드 호환성 검사임. 실게임 탐지율, 중앙 서버의
  실제 HTTPS 수신, 최종 위험도 정책 검증은 아직 완료하지 않음.

## B와 남은 합의

1. 후킹 흔적에 붙일 공통 overlap_tags의 이름과 적용 조건.
2. 양수-only 관측의 유지·만료와 검사 실패 처리. 침묵에 임의 0점을 생성하지 않음.
3. Whistle RPC 구간별 이력 필요 여부와 여러 호출 경로의 중복 후보 기준.
4. 기본 Registry 등록 후 Receiver → Scoring 및 실제 HTTPS 전송 통합 검증.

최종 위험도·가중치·임계값·자동 감점은 이번 구현 범위가 아님.

## 2026-10-03 후속 합의 — 분석 자료와 운영 정책을 구분함

인수인계 범위는 실제 이벤트 조사와 읽기 전용 분석 함수·테스트 작성임.
이 파일이 최종 Scoring 규칙을 구현하거나 상태 저장을 변경한 것은 아님.
공통 Registry 등록·저장·최종 통합 담당은 송희(B)로 이미 정해져 있음.

- 담당자 답변: 런처는 30초마다 검사하며 현재 양수만 중앙에 전송함.
  Shared 0.2.0과 가짜 수신기의 7필드·상태 보존은 담당자가 확인했다고 보고함.
  운영 서버 ACK를 A가 직접 확인한 것은 아님.
- 합의된 변경 방향: 정상 0점도 전송하고 ERROR/OFFLINE은 evidence.status를
  유지함. 실패는 정상 복귀로 사용하지 않으며 heartbeat는 개별 검사 결과를 대신하지 않음.
- whistle: 반복 양수는 누적하지 않고 latest snapshot으로 처리함.
  정상 0점으로 현재 상태를 갱신하되 과거 탐지 history는 유지하는 방향임.
- whistle_rpc: 새 로그 구간을 별도 이력으로 다루고 유효 구간 동안 현재 risk에
  반영하는 방향임. 다음 구간의 정상 결과로 과거 위반 이력을 삭제하지 않음.
  구체적 유효 시간과 로그 cursor·재시작 중복 기준은 미확정임.
- 내부 SUSPICIOUS 20 / DETECTED 60은 탐지기 등급이며 중앙 최종 판정 기준으로 확정하지 않음.

현재 분석 함수는 NORMAL 0점과 측정 불가를 구분할 수 있지만 상태 갱신·history
저장·risk 만료를 수행하지 않음. 함수의 양수-only 설명과 B 공통 emission 프로필은
전송 코드 반영 후 함께 맞춰야 함. 합의만으로 전송 필터 변경이나 서버 적용이
끝났다고 표시하지 않음.

- [x] 함수·원점수 보존 및 0점/실패 구분 테스트를 구현함.
- [x] 담당자 답변과 A·B 합의 방향을 문서에 기록함.
- [ ] 전송 변경 후 실제 중앙 수신과 공통 Scoring 연결을 검증함.
- [ ] RPC cursor·유효 시간·재시작 중복 기준과 공통 태그를 맞춤.
