# ESP 중앙 해석 정책 — A 담당

## 구현 범위

`esp.evaluate(event, baseline)`은 전송된 근거의 의미와 한계를
`PolicyAnnotations`로 반환함. 원본 Event, raw_score, B의 SignalPreview를
수정하지 않으며 최종 위험도·가중치·정상/핵 판정을 계산하지 않음.

조사 대상은 팀 PR #79의 `14b7c8ce0a70bed88e9e3daa89637ba797cc4dd4`임.
현재 작업 브랜치에 해당 탐지기가 포함되었다고 가정하지 않고,
호환성 검사는 별도 체크아웃을 명시하여 실행함.

## 점수와 근거

| 중앙 Event의 category | 현재 단일 관측 점수 | 해석 시 주의점 |
|---|---|---|
| handle_duplicate | 1 | 핸들 복제 권한 관측이며 실제 복제·ESP 실행을 증명하지 않음 |
| memory_read | 2 | 게임 메모리 읽기 권한 관측이며 우리 탐지기의 자기 탐지 여부도 확인 필요함 |
| process_tamper | 3 | 쓰기·메모리 조작·스레드 생성 권한 관측이며 실제 변조를 증명하지 않음 |
| overlay | 1 | 게임과 겹치는 외부 창 관측이며 DirectX 후킹 증거와는 다름 |
| behavioral_signal | 일반 1, 알려진 악성 해시 일치 3 | DLL 추가·메타데이터 변화·신뢰 정보이며 게임 내 플레이 행동을 뜻하지 않음 |

위 점수는 `anti_esp/team_format.py:13`의 중앙 전송 변환 기준임.
로컬 SuspicionEngine의 0~100 의심도나 감쇠·누적 결과와 혼동하지 않도록 수정함.
`team_format.py:68`의 변환기는 하나의 SensorEvent에 연결된 여러 근거를
합칠 수도 있음. 따라서 3점 초과를 무조건 거절하거나 3점으로 잘라내지 않고
확장 여부 확인을 위한 주석을 남김. 현재 실제 탐지기의 단일 관측과
합성 확장 사례(2+3=5)를 구분하여 테스트함.

## 전송 조건과 중복 처리

- 정상·미일치·지원하지 않는 관측은 변환 결과가 없으며 정상 0점 Event를
  항상 보내는 구조가 아님. 전송이 없다는 이유로 정상 처리하거나 기존 점수를
  초기화하지 않도록 주석을 남김.
- `pipeline.py:76`, `store.py:167`에서 같은 원시 이벤트 ID의 반복은 로컬
  저장 단계에서 중복 처리됨. 다른 ID로 들어온 같은 프로세스 관측은 새로운
  중앙 Event가 될 수 있으므로, 로컬 중복 처리만으로 반복 점수 문제가
  해결되었다고 해석하지 않음.
- `shared_transport.py:42`에서 outbox ID 기반 canonical UUID를 생성함.
  evidence의 sensor_event_id와 Shared 전송 event_id는 서로 다름.
- 큐 등록 성공과 실제 중앙 서버의 수신 성공은 다름. 이번 검사는 실제
  HTTPS 요청·ACK 검증을 포함하지 않음.
- 관측 이력, 만료·감쇠, 반복 관측 처리 기준은 B와 합의 필요함.
  ESP를 Godmode의 사건 누적 방식에 자동으로 연결하지 않음.

## 관측 대상 연결

| 근거 유형 | entity_key | 한계 |
|---|---|---|
| process_access | external_process:외부 PID | 프로세스 범위일 뿐 핸들·사건 ID가 아니며 PID 재사용 가능함 |
| window_overlap | overlay_window:외부 PID:HWND | 창 범위이며 실제 ESP 제품이나 DirectX 후킹 위치를 식별하지 않음 |
| module_added / module_changed / module_trust | game_module:게임 PID:정규화 경로 SHA-256 | DLL 경로 범위이며 파일 내용 해시나 로드 사건 ID가 아님 |

PID·HWND·절대 경로가 부족하면 entity_key를 만들지 않음.
DLL 이름, reason 문자열, 제목만으로 대상 ID를 추정하지 않음.
여러 category가 섞였거나 event_type과 분류가 맞지 않으면 단일 대상에
임의로 묶지 않음. overlap_tags는 B와 이름·성립 조건을 합의하기 전까지 비워 둠.
LocalGuard와 같은 entity_key가 나와도 중복 점수 제외를 자동으로 수행하지 않음.

실제 탐지 경로는 `detectors/esp_detector.py:176`(프로세스 접근),
`:240`(겹치는 창), `:345`(DLL 추가), `:357`(DLL 변화), `:420`(DLL 신뢰)에서 확인함.
초기 DLL 기준선과 실행 중 추가를 구분함. 초기 미서명 DLL은 일반 신뢰 근거로
전송하지 않지만 알려진 악성 해시 일치는 초기 관측에서도 전송될 수 있음.

## 실패 상태와 공통 프로필

현재 B의 ESP 프로필은 pending임. A의 주석을 추가하더라도 baseline의
AWAITING_DETECTOR 상태를 정상이나 정규화 완료로 바꾸지 않음.
pending 상태가 먼저 반환되어도 evidence의 ERROR/OFFLINE 또는
measurement_valid=false는 실패 주석을 남기고 대상 연결을 생략하도록 구현함.
부분 검사도 전체 정상 검사로 해석하지 않음.

현재 실제 ESP 변환기가 위 실패 Event를 항상 전송한다는 뜻은 아님.
해당 분기는 향후 명시적 상태 보고가 들어오는 경우를 위한 방어적 처리임.
최종 프로필·저장 방식 변경은 B 담당의 공통 코드에서 별도로 합의해야 함.

## 테스트와 연결

```powershell
python -B -m unittest server.scoring.tests.test_esp_policy -v
python -B server/scoring/tools/audit_esp_policy.py --repo-root ../review-pr-79
```

정책 단위 테스트는 26개, PR #79 실제 순수 변환·로컬 파이프라인과의
호환성 검사는 8개임. 호환성 검사는 합성 SensorEvent와 메모리 SQLite를
사용함. 게임·Windows 센서·실제 로그·중앙 서버에는 접근하거나 변경하지 않음.
현재 작업 브랜치 전체 Scoring 테스트 160개와 PR #79 참조 체크아웃의
공통 Scoring에 A 정책을 추가한 테스트 197개가 통과함. 서로 겹치는 테스트가
포함되므로 합산한 개수를 별도의 테스트 총수로 보고하지 않음.

```python
from server.scoring.policies import esp
registry.register("esp", esp.evaluate)
```

위 등록은 연결 예시임. 기본 Registry에 자동 등록하거나 B의 policy.py,
storage.py, main.py를 수정하지 않음. 실제 서버 적용 및 최종 점수 반영이
완료된 것으로 보고하지 않음.

## 다음 확인 사항

- [x] 단일 관측 1/2/3점과 로컬 0~100 의심도 구분함.
- [x] 원본 점수·Event·baseline을 변경하지 않는 정책과 테스트를 구현함.
- [x] PR #79 실제 변환기·로컬 중복 처리·전송 ID 생성과 호환성을 확인함.
- [ ] B와 ESP 프로필, 반복 관측·이력·만료 및 LocalGuard 중복 기준을 합의함.
- [ ] 실제 Receiver → Scoring 연결에서 정책이 호출되는지 검증함.
- [ ] 우리 읽기 전용 탐지기·UE4SS가 ESP 근거로 잡히는지 실환경에서 확인함.
- [ ] 정상·ESP 세션과 실제 HTTPS 수신/재전송으로 최종 통합 검증함.

## 2026-10-03 인수인계 범위 및 후속 합의

PR #79 조사와 이 분석 함수는 최종 점수 정책을 결정하기 위한 입력 해석 자료임.
반복 점수 집계·risk 유지/만료·가중치·공통 등록을 완료한 것은 아님.
공통 Registry 등록·프로필·저장·최종 통합은 송희(B)가 맡도록 이미 정해져 있음.
A는 필요한 분석 함수·테스트 보완 및 Receiver 연동 확인을 맡음.

상태형 탐지기의 정상 0점 전송 방향을 B와 합의함. 그러나 실제 ESP 생산자는
양수 관측 스트림이며 정상 snapshot을 항상 만들지 않으므로, 이 합의를
ESP가 이미 0점을 전송하거나 기존 ESP 사건이 자동 해소된다는 의미로 적용하지 않음.
ESP 반복 관측·정상 결과의 범위·이력과 만료는 별도로 맞춰야 함.
후속 합의와 전체 A 산출물 상태는 [A 조사 문서 9절](../A_DETECTOR_INVENTORY.md)을 참조함.

이번 문서 업로드 중 원격 `e95baf3`의 팀 main 병합을 보존하여 합침.
PR #79 실제 탐지 소스가 현재 작업 브랜치에 포함되어
`audit_esp_policy.py --repo-root .`로 동일한 호환성 8개를 다시 검증함.
병합 후 전체 Scoring 201개도 통과함. ESP 기본 등록·공통 pending 프로필 변경은 수행하지 않음.
