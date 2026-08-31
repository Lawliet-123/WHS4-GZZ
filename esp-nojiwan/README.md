# MECCHA CHAMELEON Box + Skeleton ESP

A fully external box ESP for **MECCHA CHAMELEON** (Steam / UE5.6). No DLL injection, no UE4SS dependency — it pattern-scans the running game process and reads memory through `pymem`.

## Features

- Fully external (no injected code, no hooks)
- Pattern-scans `GUObjectArray` and walks the UE object array
- Collision-capsule box ESP when the component transform is verifiable
- Explicit height/width fallback box when capsule transform data is unavailable
- Corner or 2D box style
- Skeleton ESP for the shipped paintman and LINK/newpengun assets
- Independent 30 Hz player snapshots and budgeted batch skeleton refresh
- Latest-snapshot paint path with no process-memory reads or writes
- cLeon dead-player filtering and Hunter/Survivor role filtering
- Separate configurable color for Hunters seen by a local Survivor
- Snap lines, name & distance labels
- Toggleable menu (Insert / F1)
- Separate local-player box for testing

## Requirements

- Windows 10/11
- Python 3.11+
- Game running in windowed/borderless mode

```bash
pip install pymem PyQt5 pywin32
```

## Usage

1. Launch MECCHA CHAMELEON.
2. Run the ESP:
   ```bash
   python esp.py
   ```
3. A transparent overlay will appear over the game window.
4. Use **Insert** or **F1** to show/hide the menu.
5. Toggle features from the menu.

> The ESP reads `GameState -> PlayerArray` for other players. On the home level, where no player Pawn exists yet, the overlay shows `WAITING FOR MATCH (NO PLAYER PAWN)`. Load into a match for player boxes to appear.

Run the local geometry/layout checks with:

```bash
python -m unittest -v
```

## Notes

- The Dumper-7 SDK directly declares `SkeletalMesh +0x578`, `SkinnedAsset +0x580`, `LeaderPoseComponent +0x588`, `USkeletalMeshComponent::CachedComponentSpaceTransforms +0x9B8`, `USkeletalMesh::Skeleton +0xF8`, and the `0x60`-byte LWC `FTransform` layout. Named fields are resolved from `UStruct::ChildProperties` at runtime even when the executable fingerprint changes. Blueprint-only `Mesh`, `BodyCapsule`, and `Dead` fields are resolved lazily from the first loaded player Pawn when their class is absent in the home lobby. `ComponentToWorld +0x1E0` and the native pose selector/buffers at `+0x5F0/+0x638` lie inside SDK padding, so they remain executable-fingerprint-gated. The current Steam fingerprint `(0x0A3FB000, 0x4F2390A3, 0x0A03AB06)` was verified against the live process before those native fields were enabled.
- Skeleton lines use the SDK-declared `CachedComponentSpaceTransforms` array first. Every pose payload is accepted only when two adjacent reads match, which rejects a pose being changed mid-copy (including an SDK/native alias). If the SDK header/payload is unavailable or unstable, a supported executable may fall back to only the native selector's current buffer; the alternate buffer is never guessed. Mesh/profile bindings are cached per match, unsupported profile lookups are negative-cached briefly, and adjacent metadata is bulk-read.
- All `pymem` access is read-only. A 30 Hz base worker collects camera, PlayerArray, positions, roles, and optional capsules; a separate latest-job skeleton worker performs every pose read. A blocked or slow pose read therefore cannot stop fresh player positions. The skeleton worker refreshes a fair batch of up to 16 visible actors within a 20 ms launch budget, and pending jobs overwrite older pending jobs instead of forming a backlog. Each new base frame merges the immutable pose cache using only in-process matrix math: cached world bones are moved from the sampled actor-root transform into the current actor-root transform, so a new position frame neither erases the skeleton nor leaves it behind a moving/rotating player. No process read occurs during that merge or in Qt painting. The cache is epoch-checked and lock-snapshotted, and the process handle closes only after both workers exit.
- `GameState -> PlayerArray` is accepted only after two identical header-and-payload reads. Read failures and a short same-world collapse from a previously populated array to 0/1 players do not overwrite the last complete frame. A real world/GameState transition still publishes an immediate clear frame. Pose age and base-frame age are checked again at paint time; data older than 250 ms is hidden instead of drawing known-stale geometry.
- Player candidates come only from `GameState -> PlayerArray` and must still be the pawn referenced by `PlayerState.PawnPrivate`. Persistent-level actors are not merged because they can retain unpossessed corpse pawns and non-player actors.
- The supplied Dumper-7 SDK does not declare the optional cLeon `HuntersPlayerState`, `LiveSurvivors_PlayerState`, or `MainGamePhase` fields. The reader therefore uses `GameState -> PlayerArray`, the cooked Hunter/Survivor class families, and the independently reflected `Dead` byte. Unreadable death state fails closed for that collection.
- If the local PlayerState is a Hunter, other Hunters are filtered. If it is a Survivor, Hunters use the configurable `Hunter Color` (orange by default).
- The script expects the game window title `Chameleon  `. If the title changes, update `Overlay._find_game_window()`.

Debug mode exposes `D` (dead/roster filtered), `U` (unreadable state), `R` (same-role filtered), `SKF` skeleton failure reasons, plus snapshot `READ` and `AGE` timing in milliseconds. The `T:` line splits camera (`C`), player filtering (`P`), capsule (`B`), and previous skeleton refresh (`S`) time; `SA` is refresh attempts and `SC` is the current pose-cache size.

Startup failures now produce a visible error dialog instead of closing silently. Start the game and wait for the home lobby to finish loading before running `esp.py`.

`LeaderPoseComponent` follower meshes are deliberately rejected because their leader bone-map path is not implemented. The loaded Paintman and LINK meshes checked in the current process had a null LeaderPose and returned valid 28-bone/40-bone poses; the actual match Player pawn could not be checked while the game was in the lobby.

## Disclaimer

This project is provided for **educational and research purposes only**. Using cheats or unauthorized third-party tools in online games may violate the game's Terms of Service and can result in account suspension or permanent ban. The authors assume no liability for any damages, bans, or other consequences resulting from the use or misuse of this software. Use at your own risk.
