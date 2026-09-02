# MECCHA CHAMELEON 4.0.2 호스트 God Mode

이 모듈은 팀 발표용 비공개 테스트 방에서만 사용하는 4.0.2 전용 실험 모듈이다. 공개 매치나 다른 사람의 방에서는 사용하지 않는다.

## 작동 범위

- 방을 만든 호스트 PC에서만 설치하고 실행한다.
- 보호할 Survivor의 `Invincible` 값이 켜져 있을 때 서버의 `AntiChatTrace`와 `KillPlayer` 호출을 실행 전에 중단한다.
- 게스트가 임의의 원격 호스트 판정을 우회하는 기능은 포함하지 않는다.
- 4.0.2 덤프의 오프셋만 사용하므로 다른 버전에는 넣지 않는다.

## 파일

- `GodModeHost402.dll`: x64 네이티브 모듈
- `GodModeHost402.on`: 이 빈 파일이 DLL 옆에 있을 때만 차단 기능이 켜진다.
- `GodModeHost402.log`: 로드 및 ON/OFF 상태 기록
- `godmode_host_402.cpp`: 전체 소스 코드

## 테스트 순서

1. 게임과 UE4SS를 모두 종료한다.
2. 기존 UE4SS Lua GodMode에서 F6을 눌러 보호할 로컬 Survivor의 `Invincible`을 켤 준비를 한다.
3. `GodModeHost402.dll`은 현재 `native` 폴더에 그대로 둡다.
4. DLL과 같은 폴더에 이름이 정확히 `GodModeHost402.on`인 빈 파일을 만든다. 확장명 숨김을 꺼서 `.on.txt`가 되지 않게 한다.
5. 게임을 실행하고 본인이 방을 만든다. 공개 매치가 아닌 비공개 테스트 방을 사용한다.
6. Survivor로 들어간 뒤 UE4SS에서 `godmode on`을 입력하거나 F6을 눌러 `Invincible=true`를 만든다.
7. PowerShell을 열고 아래 명령을 **한 번만** 실행한다. 경로는 줄을 나누지 말고 그대로 붙여넣는다.

```powershell
& "C:\Users\LG\Desktop\화햇 프로젝트 복사본\Meccha-Chameleon-Tools\camouflage-lighter\native\runtime-injector.exe" "PenguinHotel-Win64-Shipping.exe" "C:\Users\LG\Desktop\화햇 프로젝트 복사본\MECCHA-CHAMELEON-UE4SS-MOD\native\GodModeHost402.dll"
```

   `injected pid=...`가 나오면 로드는 성공한 것이다. `OpenProcess failed`가 나오면 게임과 PowerShell의 관리자 권한 수준을 동일하게 맞춘다.
8. DLL 옆의 `GodModeHost402.log`에 `loaded`와 `GodMode ON`이 기록됐는지 확인한다.
9. 팀원의 Hunter가 보호된 Survivor에게 한 발만 쏘고 생존 여부를 확인한다.
   성공하면 로그에 `blocked server death call; total=...`이 추가된다.
10. 시험이 끝나면 `GodModeHost402.on` 파일을 삭제하고 게임을 종료한다.

## 실패 시 확인

- 로그 파일이 없으면 DLL이 로드되지 않은 것이다.
- 로그에 `GodMode OFF`가 뜨면 `.on` 파일 이름이나 위치가 틀린 것이다.
- Lua 상태에 `Invincible=false`가 보이면 먼저 `godmode on`을 실행한다.
- 게스트 Survivor를 보호하려는 시험이라면 현재 설계 대상이 아니다. 호스트가 Survivor인 조건부터 검증한다.
- 게임이 튕기면 재주입하지 말고 로그와 UE4SS 로그를 보존한 뒤 DLL과 `.on` 파일을 제거한다.
