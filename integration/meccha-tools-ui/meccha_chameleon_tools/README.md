# MECCHA CHAMELEON Tools 실행 방법

MECCHA CHAMELEON 4.0.2와 통합 Tools UI를 함께 실행하기 위한 방법입니다.

Steam을 먼저 실행해 둡니다.

PowerShell을 열고 프로젝트 폴더로 이동합니다.

```powershell
cd "C:\Users\LG\Desktop\WHS4-GZZ-integration\integration\meccha-tools-ui"
```

처음 실행하는 경우 필요한 Python 패키지를 설치합니다.

```powershell
pip install -r requirements.txt
```

게임과 Tools UI를 같이 실행하려면 아래 명령을 실행합니다.

```powershell
python -m meccha_chameleon_tools.launcher
```

정상적으로 실행되면 다음 두 프로그램이 함께 실행됩니다.

```text
MECCHA CHAMELEON 4.0.2
MECCHA CHAMELEON Tools UI
```

게임이 이미 실행 중인 경우에는 게임을 중복 실행하지 않고 Tools UI만 실행됩니다.


## UI만 실행

게임을 실행하지 않고 Tools UI만 확인하려면 아래 명령을 실행합니다.

```powershell
cd "C:\Users\LG\Desktop\WHS4-GZZ-integration\integration\meccha-tools-ui"
python -m meccha_chameleon_tools.module_selector
```


## 게임 실행 상태 확인

현재 MECCHA CHAMELEON이 실행 중인지 확인하려면 아래 명령을 실행합니다.

```powershell
cd "C:\Users\LG\Desktop\WHS4-GZZ-integration\integration\meccha-tools-ui"
python -m meccha_chameleon_tools.game_session
```

게임이 꺼져 있는 경우:

```text
Status      : OFFLINE
Running     : False
Game files  : True
```

게임이 실행 중인 경우:

```text
Status      : CONNECTED
Running     : True
Game files  : True
```


## 기본 게임 경로

현재 프로그램은 아래 경로에 MECCHA CHAMELEON이 설치되어 있는 것을 기준으로 합니다.

```text
C:\Program Files (x86)\Steam\steamapps\common\MECCHA CHAMELEON
```

게임이 다른 위치에 설치되어 있다면 아래 파일의 `GAME_ROOT` 값을 실제 게임 설치 위치로 변경해야 합니다.

```text
meccha_chameleon_tools/game_session.py
```


## 가장 간단한 실행 방법

Steam을 실행한 뒤 아래 두 줄만 입력하면 됩니다.

```powershell
cd "C:\Users\LG\Desktop\WHS4-GZZ-integration\integration\meccha-tools-ui"
python -m meccha_chameleon_tools.launcher
```