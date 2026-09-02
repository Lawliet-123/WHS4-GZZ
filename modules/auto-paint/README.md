# Auto Paint

게임 안의 숨은 캐릭터 표면을 현재 화면의 배경색에 가깝게 자동 도색하는 독립 모듈입니다. 기존 Meccha Chameleon Tools 전체를 실행하지 않아도 단독 GUI로 사용할 수 있으며, 이후 통합 UI에서는 `AutoPaintEngine`만 불러와 사용할 수 있습니다.

## 구현 방식

- Python: 게임 프로세스 확인, DLL 주입 요청, 브리지 통신, 캡처·도색 명령, GUI와 진단 ZIP 생성
- C++ DLL (`runtime-bridge.dll`): 게임 프로세스 안에서 UE 5.6.1 SDK 구조를 읽고 Auto Paint를 실행
- C++ Injector (`runtime-injector.exe`): 대상 PID와 실행 파일을 검증한 뒤 DLL을 주입하고 `BridgeStartV1`을 호출
- Mesh profile JSON: 메시의 정점 인덱스, UV, 본 가중치 등 Runtime Triangle Cache 대체 재구성에 필요한 정적 데이터

Python과 인젝터 사이에는 다음 직접 실행 규약을 사용합니다.

```text
runtime-injector.exe --direct <pid> <creation-filetime-utc> <expected-exe-path> <bridge-path>
```

이 모듈의 인젝터와 브리지는 둘 다 `BridgeStartV1`/128바이트 시작 블록을 사용합니다. 최신 상류의 `BridgeStartV2` 소스나 예전 2인자 인젝터를 한쪽에만 교체하면 서로 호환되지 않습니다.

## 폴더 구성

```text
auto-paint/
├── README.md
├── Scripts/
│   ├── auto_paint.py
│   ├── RUN_AUTO_PAINT.bat
│   ├── native/
│   │   ├── runtime-bridge.dll
│   │   └── runtime-injector.exe
│   └── mesh-profiles/
│       ├── paintman.mesh-profile-v2.json
│       └── paintman_cube.mesh-profile-v2.json
├── src/
│   ├── bridge/bridge.cpp
│   ├── injector/injector.cpp
│   └── include/
│       ├── sdk.hpp
│       └── direct_bridge_abi.hpp
├── AutoPaint.Native.sln
├── AutoPaint.Bridge.vcxproj
├── AutoPaint.Injector.vcxproj
├── build.ps1
├── requirements.txt
└── LICENSE.txt
```

## 필요한 프로그램 및 라이브러리

단독 실행:

- Windows 10/11 x64
- Python 3.x (Windows용 기본 설치에 포함되는 `tkinter` 필요)
- 별도 PyPI 패키지는 필요하지 않음

네이티브 코드 재빌드:

- Visual Studio 2022 또는 Build Tools 2022
- `Desktop development with C++` 워크로드
- MSVC v143 이상 및 Windows 10/11 SDK (`build.ps1`이 설치된 도구 집합을 자동 선택)

브리지에서 사용하는 Windows 라이브러리는 `Ws2_32`, `Gdi32`, `User32`이며, 인젝터는 `Bcrypt`를 사용합니다.

## 실행 방법

1. 게임을 먼저 실행하고 실제 플레이 화면까지 들어갑니다.
2. `Scripts/RUN_AUTO_PAINT.bat`를 더블 클릭합니다.
3. 창에서 **Start Painting**을 누릅니다.

PowerShell에서 실행하려면 저장소 루트에서 다음과 같이 입력합니다.

```powershell
py -3 .\modules\auto-paint\Scripts\auto_paint.py
```

기본 대상 프로세스는 `PenguinHotel-Win64-Shipping.exe`입니다. 이름이 다른 빌드는 다음처럼 지정할 수 있습니다.

```powershell
py -3 .\modules\auto-paint\Scripts\auto_paint.py --game-process "게임프로세스.exe"
```

## 사용 방법 / 단축키

- `Start Painting`: 브리지 연결 후 현재 화면을 캡처하고 도색 시작
- `Stop Painting`: 진행 중인 도색 요청 취소
- `진단 ZIP 만들기`: 로그, 인젝터 결과, 마지막 응답 및 환경 정보를 ZIP으로 수집
- 전역 키보드 단축키는 현재 없음

## ON/OFF 방법

- ON: `Start Painting`
- OFF: `Stop Painting`
- 프로그램 종료 시에는 브리지 연결을 종료합니다.

`Stop Painting`은 진행 중 작업을 멈추는 기능이며 이미 게임에 적용된 색을 원래대로 지우는 기능은 아닙니다. 적용 결과의 초기화 시점은 게임의 라운드 전환·캐릭터 재생성 동작을 따릅니다.

## 네이티브 코드 빌드

모듈 폴더에서 다음 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

또는 `AutoPaint.Native.sln`을 Visual Studio 2022로 열어 `Release | x64`를 빌드합니다. 결과물은 자동으로 `Scripts/native/`에 생성됩니다.

인젝터 소스는 Auto Paint 브리지와 호환되는 상류 커밋 `921a934a0e32c84bf59dbf4d11232265af44da6c`의 `BridgeStartV1` 구현으로 고정했습니다. 인젝터만 최신 `BridgeStartV2`로 올리려면 브리지와 Python 시작 블록 직렬화도 함께 V2로 변경해야 합니다.

## 기존 Meccha Chameleon Tools UI에 통합

기존 구현을 독립 모듈로 유지하려면 통합 UI는 `AutoPaintEngine`을 감싸는 버튼만 제공하면 됩니다.

```python
import sys
from pathlib import Path

module_scripts = Path("modules/auto-paint/Scripts").resolve()
sys.path.insert(0, str(module_scripts))

from auto_paint import AutoPaintEngine

engine = AutoPaintEngine()

# UI 작업 스레드가 아닌 별도 작업 스레드에서 호출
paint_result = engine.paint()
cancel_result = engine.cancel()

# 통합 프로그램이 종료될 때 한 번 호출
engine.shutdown()
```

`paint()`는 캡처와 서버 배치 처리를 기다리므로 UI 메인 스레드에서 직접 호출하지 말고 작업 스레드에서 실행해야 합니다. 통합 UI의 권장 연결은 다음과 같습니다.

- Auto Paint ON 버튼 → 작업 스레드에서 `engine.paint()`
- Auto Paint OFF 버튼 → `engine.cancel()`
- 진단 버튼 → `engine.create_diagnostic_bundle("manual")`
- 프로그램 종료 이벤트 → `engine.shutdown()`

## 테스트한 버전 및 범위

- 게임 UI 표시 버전: `4.1.0`
- Unreal Engine SDK 기준: `5.6.1`
- 플랫폼: Windows x64
- 단독 GUI 실행, V1 인젝터/브리지 빌드, 네이티브 프로토콜 정적 검증 완료
- 실제 게임에서 Auto Paint 실행 및 브리지 연결 확인
- 연속 커버리지 조정본의 모든 맵·모든 캐릭터 조합에 대한 회귀 테스트는 추가로 필요

## 호스트 / 클라이언트 동작 차이

브리지는 로컬 적용과 게임의 `ServerPaintBatch` 경로를 함께 사용합니다. 호스트 권한, 게임 모드, 대상 액터의 소유권에 따라 다른 플레이어에게 보이는 결과나 반영 시점이 달라질 수 있습니다. 현재 호스트와 원격 클라이언트의 모든 조합은 검증되지 않았으므로 멀티플레이 통합 전 별도 테스트가 필요합니다.

## 로그와 오류 전달

실행 로그와 진단 파일은 `%LOCALAPPDATA%/MecchaAutoPaintOnly/` 아래에 생성됩니다. 오류가 나면 GUI의 **진단 ZIP 만들기**를 눌러 생성된 ZIP을 전달하면 다음 내용을 함께 확인할 수 있습니다.

- 대상 프로세스와 실행 파일 경로
- 인젝터 표준 출력/오류 및 반환 코드
- 브리지 연결 상태와 포트
- DLL·인젝터·mesh profile 해시
- 마지막 Auto Paint 요청과 단계별 응답

## 출처 및 라이선스

이 모듈은 다음 공개 프로젝트의 구조와 코드를 바탕으로 수정되었습니다.

- SilentJMA/Meccha-Chameleon-Tools
- acentrist/MecchaCamouflage

직접 프로토콜 인젝터와 관련 네이티브 코드는 GNU GPL v3 이상 조건의 영향을 받습니다. 배포 시 `LICENSE.txt`와 저작권·출처 고지를 유지해야 하며, 공식 상류 릴리스로 오인되게 표시해서는 안 됩니다.
