# Detector Scoring Policies

탐지기별 점수 정책을 구현하는 폴더입니다.

## 담당

송희:
- noclip.py
- aimbot.py
- autopaint.py
- godmode.py

A 담당자:
- localguard.py
- whistle.py
- hide_anywhere.py
- esp.py

## 공통 규칙

1. 각 담당자는 자신의 탐지기 정책과 테스트를 작성합니다.
2. 입력은 Shared의 공통 7필드 이벤트를 기준으로 합니다.
3. 탐지기의 원시 점수를 임의로 변경하지 않습니다.
4. 서로 다른 탐지기의 점수를 임의로 합산하지 않습니다.
5. 공통 policy.py와 storage.py는 사전 협의 없이 수정하지 않습니다.
6. 실제 전송되는 module 이름을 확인하여 사용합니다.

## 현재 상태

- `contract.py`: A/B 공통 읽기 전용 정책 분석 인터페이스
- `noclip.py`: Shared 0.2.0 기준 Noclip 스냅샷 주석
- `aimbot.py`: 라운드 누적 Aimbot 스냅샷 주석
- `autopaint.py`: integrity/behavior 두 채널 AutoPaint 주석
- `godmode.py`: Godmode 최신 변경 병합 후 작성 예정

`policy.py`의 B2a 프로필은 실제 전송 의미가 바뀐 경우에만 함께 갱신합니다.
최종 위험도/가중치/판정은 아직 이 폴더에서 계산하지 않습니다.

## 공통 정책 분석 계약 (B2b 후속)

`contract.py`의 `PolicyRegistry`를 사용합니다. **기존 `policy.py`는 이동하거나
수정하지 않습니다.** 그 파일에서 제공하는 `inspect_event()`를 먼저 실행하여
미확정·측정 실패 상태와 모듈별 원시 점수 해석을 보존합니다.

담당 탐지기별 파일은 다음 서명을 가진 함수를 구현합니다.

```python
from server.scoring.policies.contract import PolicyAnnotations

def evaluate(event, baseline) -> PolicyAnnotations:
    # event: 정확한 공통 7필드, baseline: 기존 B2a 분석 결과
    # 모듈별 점수/실제 사건의 중복 여부를 아직 임의로 확정하지 않습니다.
    return PolicyAnnotations(
        entity_key=None,       # 필요한 경우 PID 등 관측 대상 키
        overlap_tags=(),       # 중복 가능성을 검토할 후보 태그
        notes=(),              # 모듈별 상태/한계에 대한 설명
    )
```

송희(B)가 이후 통합 시 하나의 등록소에 함수들을 연결합니다.

```python
from server.scoring.policies.contract import PolicyRegistry
from server.scoring.policies import noclip

registry = PolicyRegistry()
registry.register("noclip", noclip.evaluate)
result = registry.evaluate(event)
print(result.signal, result.annotations)
```

- `register()`에는 실제 Shared 이벤트의 정확한 `module` 값을 사용합니다.
  `localguard`라는 임의 대표 이름 대신 `external_access`, `localguard_yara`
  등의 실제 값으로 각각 등록하는 방식이 필요합니다.
- 한 module을 중복 등록하면 오류가 발생합니다.
- `overlap_tags`는 **중복 의심 표시**일 뿐, 중복 사건 확정이나 가중치가 아닙니다.
- 이 인터페이스는 읽기 전용 분석을 위한 것입니다. `process()` 저장 경로,
  SQLite 테이블, Dashboard API에는 자동 연결되지 않습니다.
- `raw_fraction_pct`는 기존 B2a와 동일하게 **자체 척도 대비 비율**입니다.
  최종 위험도, 점수 정규화, 판정은 테스트 데이터와 팀 기준이 합의된 뒤
  별도 API로 구현합니다.
