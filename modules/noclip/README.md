# MecchaNoclip

MECCHA CHAMELEON 환경에서 UE4SS Lua를 이용해 구현한 Noclip입니다.


## Requirements

- MECCHA CHAMELEON
- UE4SS
- Lua 기반 UE4SS Mod 환경

> 이 저장소에는 UE4SS 자체가 포함되어 있지 않습니다.
> Noclip 모드를 사용하려면 먼저 게임에 UE4SS를 설치해야 합니다.

## Repository Structure

```text
WHS4-GZZ/
└── MecchaNoclip/
    ├── README.md
    └── Scripts/
        └── main.lua
```

`WHS4-GZZ`는 GitHub 저장소이며, 실제 게임에 설치할 때는 저장소 전체가 아니라 `MecchaNoclip` 폴더를 사용합니다.

## Installation

### 1. UE4SS 설치

MECCHA CHAMELEON 게임 폴더에 UE4SS를 먼저 설치합니다.

UE4SS 설치가 완료되면 게임 폴더에 `Mods` 폴더가 존재하는지 확인합니다.

### 2. MecchaNoclip 복사

이 저장소의 `MecchaNoclip` 폴더를 UE4SS의 `Mods` 폴더 안에 복사합니다.

최종적으로 다음과 같은 구조가 되어야 합니다.

```text
MECCHA CHAMELEON Game Folder/
└── Mods/
    ├── mods.txt
    └── MecchaNoclip/
        └── Scripts/
            └── main.lua
```

### 3. 모드 활성화

`Mods/mods.txt`를 열어 다음 항목을 추가하거나 활성화합니다.

```text
MecchaNoclip : 1
```

다른 사용자 모드를 함께 사용하지 않을 경우 해당 모드들은 비활성화합니다.

UE4SS 구동에 필요한 기본 구성은 임의로 삭제하지 않는 것을 권장합니다.

## Usage

게임 실행 후 UE4SS 콘솔에서 다음 명령어를 입력합니다.

```text
nocliptest
```

한 번 입력하면 Noclip이 활성화되고, 다시 입력하면 비활성화됩니다.

### Controls

| Key | Action |
|---|---|
| W | Forward |
| S | Backward |
| A | Left |
| D | Right |
| Space | Up |
| C | Down |

## Implementation

Noclip 활성화 시 캐릭터의 `BodyCapsule` Collision을 비활성화합니다.

```lua
capsule:SetCollisionEnabled(0)
```

중력의 영향을 받지 않도록 Gravity를 0으로 설정합니다.

```lua
pawn:SetGravity(true, {
    X = 0.0,
    Y = 0.0,
    Z = 0.0
})
```

이동은 일반적인 캐릭터 Movement 기능 대신 현재 Pawn의 위치와 회전값을 가져와 새로운 좌표를 계산한 뒤 직접 위치를 변경하는 방식으로 구현했습니다.

```lua
local location = pawn:K2_GetActorLocation()
local rotation = pawn:K2_GetActorRotation()

pawn:K2_SetActorLocation(
    newLocation,
    false,
    {},
    true
)
```

Noclip을 비활성화하면 기존 Collision 값과 Gravity 상태를 복구합니다.

## Multiplayer Test

테스트 결과 호스트 환경에서는 Noclip을 이용한 위치 변경이 유지되었습니다.

비호스트 클라이언트에서는 로컬 화면상 벽을 통과할 수 있었지만, 이후 서버에 의해 기존 위치로 보정되는 현상을 확인했습니다.

```text
Host
→ Collision disabled
→ Position change
→ Position maintained

Client
→ Collision disabled locally
→ Position change
→ Server correction
→ Position restored
```

이를 통해 캐릭터 이동에 서버 측 위치 검증 또는 서버 권한 기반 보정이 적용되고 있는 것으로 추정할 수 있습니다.


## Notes

현재 이동은 `K2_SetActorLocation()`을 통한 직접적인 위치 변경 방식이므로 일반적인 걷기 및 달리기 애니메이션은 재생되지 않습니다.

또한 호스트가 아닐 경우 월활히 작동하지 않을 수 있습니다.

UE4SS는 이 저장소에 포함하지 않으며, 사용자가 별도로 설치해야 합니다.
