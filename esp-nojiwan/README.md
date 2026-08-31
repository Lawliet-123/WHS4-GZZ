# MECCHA CHAMELEON Box + Skeleton ESP

A fully external box ESP for **MECCHA CHAMELEON** (Steam / UE5.6). No DLL injection, no UE4SS dependency — it pattern-scans the running game process and reads memory through `pymem`.

## Features

- Fully external (no injected code, no hooks)
- Pattern-scans `GUObjectArray` and walks the UE object array
- Collision-capsule box ESP when the component transform is verifiable
- Explicit height/width fallback box when capsule transform data is unavailable
- Corner or 2D box style
- Skeleton ESP for the shipped paintman and LINK/newpengun assets
- Fast player/capsule snapshots with budgeted round-robin skeleton refresh
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
python -m pip install -r requirements.txt
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
python -m unittest -v test_esp_geometry.py
```

## Notes

- The Dumper-7 SDK directly declares `SkeletalMesh +0x578`, `SkinnedAsset +0x580`, `LeaderPoseComponent +0x588`, `USkeletalMeshComponent::CachedComponentSpaceTransforms +0x9B8`, `USkeletalMesh::Skeleton +0xF8`, and the `0x60`-byte LWC `FTransform` layout. `ComponentToWorld +0x1E0` and the native pose selector/buffers at `+0x5F0/+0x638` lie inside SDK padding, so they remain executable-fingerprint-gated rather than being described as SDK-reflected fields.
- Skeleton lines use the SDK-declared `CachedComponentSpaceTransforms` array first. Every pose payload is accepted only when two adjacent reads match, which rejects a pose being changed mid-copy (including an SDK/native alias). If the SDK header/payload is unavailable or unstable, a supported executable may fall back to only the native selector's current buffer; the alternate buffer is never guessed. Mesh/profile bindings are cached per match, unsupported profile lookups are negative-cached briefly, and adjacent metadata is bulk-read.
- All `pymem` access is read-only and runs on one worker. Each cycle first publishes a complete box-only player/capsule frame. Optional cached poses are then transformed with each actor's freshly verified mesh `ComponentToWorld` and, while the base is still fresh, published as a second enriched snapshot. Only after that does the worker sample at most one visible actor from an actor-ID round-robin queue for a future cycle. Thus neither pose sampling nor cached-pose transform reads can hold the new box frame behind them, and old world-space bones cannot trail a moving or rotating player. Pose age is checked again at paint time and anything older than 250 ms is hidden. Mesh changes, unreadable transforms, stale base frames, expired poses, and a world/GameState change during collection fail closed. The Qt paint event only projects and draws immutable snapshots; data older than 250 ms from collection start is hidden instead of drawing known-stale geometry.
- Player candidates come only from `GameState -> PlayerArray` and must still be the pawn referenced by `PlayerState.PawnPrivate`. Persistent-level actors are not merged because they can retain unpossessed corpse pawns and non-player actors.
- The supplied Dumper-7 SDK does not declare the optional cLeon `HuntersPlayerState`, `LiveSurvivors_PlayerState`, or `MainGamePhase` fields. This build therefore uses `GameState -> PlayerArray`, the cooked Hunter/Survivor class families, and the independent stable `Dead` byte directly instead of scanning runtime reflection metadata. Unreadable death state fails closed for that collection.
- If the local PlayerState is a Hunter, other Hunters are filtered. If it is a Survivor, Hunters use the configurable `Hunter Color` (orange by default).
- The script expects the game window title `Chameleon  `. If the title changes, update `Overlay._find_game_window()`.

Debug mode exposes `D` (dead/roster filtered), `U` (unreadable state), `R` (same-role filtered), `SKF` skeleton failure reasons, plus snapshot `READ` and `AGE` timing in milliseconds. The `T:` line splits camera (`C`), player filtering (`P`), capsule (`B`), and previous skeleton refresh (`S`) time; `SA` is refresh attempts and `SC` is the current pose-cache size.

Startup failures now produce a visible error dialog instead of closing silently. Start the game and wait for the home lobby to finish loading before running `esp.py`.

`LeaderPoseComponent` follower meshes are deliberately rejected because their leader bone-map path is not implemented. The loaded Paintman and LINK meshes checked in the current process had a null LeaderPose and returned valid 28-bone/40-bone poses; the actual match Player pawn could not be checked while the game was in the lobby.

## Disclaimer

This project is provided for **educational and research purposes only**. Using cheats or unauthorized third-party tools in online games may violate the game's Terms of Service and can result in account suspension or permanent ban. The authors assume no liability for any damages, bans, or other consequences resulting from the use or misuse of this software. Use at your own risk.
