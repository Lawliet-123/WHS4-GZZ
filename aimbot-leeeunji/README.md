# Bounds 기반 대상 탐색 및 회전 모듈

Unreal Engine 기반 테스트 환경에서 로컬 캐릭터, 다른 플레이어, 카메라 및 `ControlRotation`을 찾고, 유효한 Survivor의 Bounds 중심을 기준으로 대상을 선택하는 Python 모듈입니다.

## 환경

- 운영체제: Windows
- Python: 3.10 이상 권장
- 대상 프로세스: `PenguinHotel-Win64-Shipping.exe`
- 외부 의존성: `Pymem 1.14.0`
- 활성화 키: F8

`engine.py`의 시그니처와 부트스트랩 오프셋은 검증 당시의 게임 빌드를 기준으로 합니다. 게임 업데이트 후에는 재검증이 필요할 수 있습니다.

## 파일 구조

```text
.
├─ engine.py
├─ step3_other_players_positions.py
├─ step5_target_angle.py
├─ step8_fov_target.py
├─ step9_aim_trace.py
├─ test_aim_trace.py
├─ test_fov_target.py
├─ requirements.txt
├─ .gitignore
└─ README.md
```

### 주요 파일

- `engine.py`: 프로세스 연결, 패턴 스캔, NamePool, Reflection, 좌표·카메라·Bounds 읽기, `ControlRotation` 읽기/쓰기
- `step3_other_players_positions.py`: PlayerState 순회, Pawn 좌표·거리·클래스·상태·Bounds 수집
- `step5_target_angle.py`: 목표 방향 벡터를 Pitch·Yaw로 변환
- `step8_fov_target.py`: 월드 좌표의 화면 투영, Hunter·SpectatePawn·탈락자 제외, Bounds 중심 기반 후보 선정
- `step9_aim_trace.py`: 최종 실행 진입점, F8 입력, 가까운 대상 우선 및 잠금, 회전 속도 제한, 쓰기 결과 로그
- `test_*.py`: 화면 투영, 대상 선택, 각도 정규화 및 회전 단계 테스트

## 설치

PowerShell에서 저장소 폴더로 이동한 뒤 가상 환경을 만듭니다.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## 실행 전 테스트

```powershell
py -m unittest discover -v
```

총 11개의 테스트가 통과해야 합니다. 이 테스트는 실제 게임 프로세스에 연결하지 않고 수학 계산과 선택 로직을 검증합니다.

## 실행

1. 테스트 대상 프로세스를 실행하고 로컬 Pawn이 생성된 맵에 진입합니다.
2. Python을 대상 프로세스와 동일한 권한 수준으로 실행합니다.
3. 아래 명령을 실행합니다.

```powershell
py step9_aim_trace.py --radius 1000
```

기본 FOV 반경은 700px이며, 위 예시는 넓은 탐색 범위를 위해 1000px을 사용합니다. 유효한 대상이 있는 상태에서 F8을 누르면 회전값이 기록됩니다. F8을 놓거나 유효한 대상이 없으면 값을 기록하지 않습니다.

실행 중 기본적으로 `aim_trace.csv`가 생성됩니다. CSV는 `.gitignore`에 포함되어 Git에 추가되지 않습니다.

### 주요 옵션

```text
--width       게임 화면 너비, 미지정 시 자동 탐지
--height      게임 화면 높이, 미지정 시 자동 탐지
--radius      화면 FOV 반경(px), 기본 700
--aim-height  Bounds 읽기 실패 시 사용할 높이 보정값, 기본 80
--speed       초당 최대 회전 각도, 기본 120
--hz          초당 처리 횟수, 기본 60
--status-hz   초당 콘솔 상태 출력 횟수, 기본 5
--output      CSV 로그 경로, 기본 aim_trace.csv
```

## 통합 안내

팀 프로젝트에서 기능별로 통합할 때는 다음 진입점을 사용합니다.

```python
from engine import GameLink
from step3_other_players_positions import list_other_players
from step5_target_angle import direction_to_angles
from step8_fov_target import FovTargetSelector, collect_candidates
from step9_aim_trace import normalize_angle, shortest_delta, step_rotation
```

- 엔진 연결: `link = GameLink()`
- World: `link.get_world()`
- 로컬 Pawn: `link.get_local_pawn(world)`
- 로컬 Controller: `link.get_local_controller(world)`
- 카메라: `link.get_camera(world)`
- 현재 회전: `link.get_control_rotation(controller, world)`
- 회전 기록: `link.set_control_rotation(rotation, controller, world)`
- 플레이어 수집: `list_other_players(link)`
- 대상 후보: `collect_candidates(...)`

`GameLink`는 실행 초기에 패턴 스캔과 필드 해결을 수행하므로 프레임마다 새로 만들지 말고 하나의 인스턴스를 공유하는 구조가 적합합니다.

## 대상 선별 기준

최종 후보에는 다음 조건을 모두 만족하는 캐릭터만 포함됩니다.

- 로컬 Pawn이 아닐 것
- cLeon Character 계열일 것
- Hunter가 아닐 것
- SpectatePawn이 아닐 것
- `IsLiveSelf` 값이 참일 것
- 카메라 앞쪽이며 FOV 범위 안에 있을 것

조준점은 `FBoxSphereBounds.Origin`을 우선 사용하고, Bounds가 유효하지 않은 경우에만 Pawn 원점에 `aim-height`를 더한 값을 사용합니다.

## 주의사항

- 시그니처와 오프셋은 게임 빌드 업데이트에 의해 변경될 수 있습니다.
- 화면 해상도 자동 탐지가 정확하지 않으면 `--width`와 `--height`를 직접 지정합니다.
- 실행 권한이 대상 프로세스보다 낮으면 메모리 연결이 실패할 수 있습니다.
- 팀에서 승인한 개발·테스트 환경에서만 사용하고 관련 약관과 규칙을 확인하세요.
