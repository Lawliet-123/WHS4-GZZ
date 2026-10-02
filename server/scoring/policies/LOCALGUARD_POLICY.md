# LocalGuard 중앙 해석 정책 — A 담당

## 1차 작업: 외부 접근 채널 분리

`localguard.evaluate(event, baseline)`은 Shared 7필드 Event와 기존 Scoring
점검 결과를 받아 `PolicyAnnotations`만 반환함. 원점수·등급·저장 내용·최종
판정을 수정하지 않음. 기본 Registry 등록은 B 담당과 협의 후 반영해야 함.

| module / evidence.submodule | 의미 | entity_key |
|---|---|---|
| external_access / external_process | 외부 프로세스의 위험 핸들 관측 | 양수·유효 PID인 경우 `external_process:<source_pid>` |
| external_access / module_integrity | DLL 추가·매핑 변경·초기 기준선 감사 | 양수·알려진 변화·유효 PID/절대 Windows 경로인 경우 `game_module:<target_pid>:<경로 SHA-256>` |
| 실패 관측·0점·미분류 채널 | 정상·위험 해소로 자동 변환하지 않음 | 없음 |

- 핸들 채널은 팀 main `bd66c6524b6dc8d50ded0c052191a70c8b4acf7c`의
  `client/LocalGuard/external_access/process_access/detector.py:27`,
  `runner.py:168`, `runner.py:251` 기준으로 확인함.
- DLL 채널은 아직 미병합인 PR #78의 head
  `f6bf7d1e1fb40774fff8c6fe64bfb23fef329f50` 기준임.
  `module_integrity/detector.py:37`, `:49`, `runner.py:348`, `:570`의
  전송 계약을 해석함. 탐지기 자체를 main에 병합하거나 수정한 것은 아님.
- 핸들의 NORMAL 0점은 이번 검사에 양수 결과가 없다는 의미임. DLL의 NORMAL
  0점은 새 의심 변화가 없다는 의미임. 이전 DLL이 제거되었다는 증거가 아님.
- DLL 키의 SHA-256은 정규화된 **경로 문자열**을 짧게 표현하는 수단임.
  파일 바이트 해시·악성 신뢰 판정·개별 DLL 로드 사건 ID로 사용하지 않음.
  경로를 실제 열거나 서버의 파일 시스템에서 조회하지 않음.
- 같은 이름 `external_access`를 쓰는 두 하위 채널은 최신 module 상태를
  덮어쓸 수 있음. annotations의 entity_key를 붙여도 SQLite 저장 구조가
  바뀌는 것은 아님. submodule 저장 분리·변화 이력은 B와 합의 필요함.
- overlap_tags는 합의 전까지 빈 tuple임. PID/경로의 일치만으로 중복 탐지를
  확정하거나 점수를 합산/감점하지 않음.

## 테스트·연결

```powershell
python -B -m unittest server.scoring.tests.test_localguard_policy -v
```

테스트는 독립 Registry를 만들어 `external_access`를 등록함. 실게임 탐지율,
실제 중앙 HTTPS 수신, 최종 점수 정책, 운영 저장 분리를 검증한 것이 아님.
1차 검증: 외부 접근 정책 21개 + Whistle 25개 + 공통 계약 11개, 총 57개 통과함.

```python
from server.scoring.policies import localguard
registry.register("external_access", localguard.evaluate)
```

공통 `registry.py`, `contract.py`, `policy.py`, `storage.py`, `main.py`는 변경하지 않음.
YARA·실행 파일 해시·메모리 관측 정책은 다음 작업 단위에서 추가 예정임.
