## 9. 처음부터 실행하는 방법

### 9.1 사전 준비

1. 게임과 UE4SS를 모두 종료한다.
2. 작업 관리자에서 `PenguinHotel-Win64-Shipping.exe`가 없는지 확인한다.
3. 게임 화면에서 대상 버전이 4.0.2인지 확인한다.
4. `GodModeHost402_v4.dll`, `godmode-simple-injector.exe`, `RUN_GODMODE_402_FIXED.cmd`, `GodModeHost402.on`이 같은 `native` 폴더에 있는지 확인한다.
5. 이전 `GodModeHost402.log`를 삭제하여 이번 시험 로그와 구분한다.

### 9.2 게임과 Lua God Mode 실행

1. 게임을 실행한다.
2. 실험자가 직접 비공개 방을 만든다.
3. 실험자는 호스트이면서 Survivor 역할을 선택한다.
4. 팀원 한 명은 Hunter 역할을 선택한다.
5. Survivor Pawn이 완전히 생성된 다음 UE4SS Console에서 `godmode on`을 입력한다.
6. `godmode status`를 입력하거나 접속 로그를 확인하여 `Invincible=true`, `Dead=false`, `Health=100`인지 확인한다.

### 9.3 네이티브 DLL 로드

1. `RUN_GODMODE_402_FIXED.cmd`를 한 번만 실행한다.
2. `[SUCCESS] GodModeHost402.dll was loaded.` 메시지를 확인한다.
3. 같은 게임 실행 중에는 인젝터를 다시 실행하지 않는다.
4. `GodModeHost402.log`에서 다음 로그를 확인한다.

```text
GodModeHost402 DLL_PROCESS_ATTACH
GodModeHost402 worker started
GodModeHost402 AppendString initialized
GodModeHost402 loaded; create GodModeHost402.on beside DLL to enable
GodMode ON
ProcessEvent hook installed; total=1
```

`ProcessEvent hook installed`가 없으면 실제 사망 차단이 준비되지 않은 상태이므로 총격 시험을 중단한다.

### 9.4 총격 시험

1. Hunter와 Survivor가 가까운 거리에서 정지한다.
2. Hunter가 Survivor에게 한 발만 발사한다.
3. Survivor가 사망, 관전, 라운드 제거 상태로 전환되는지 확인한다.
4. 로그에서 다음 줄이 생성되는지 확인한다.

```text
blocked server death call; total=1
```

5. 성공 시 추가 사격으로 `total` 값이 증가하는지 확인한다.
6. 실패하거나 게임이 종료되면 재주입하지 않고 GodMode 로그와 UE4SS 로그를 보존한다.
