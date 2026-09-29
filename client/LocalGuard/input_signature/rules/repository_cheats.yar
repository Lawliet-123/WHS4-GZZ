// WHS4-GZZ 저장소의 알려진 핵 빌드에 맞춘 '코드 존재' 시그니처다.
// 기준 소스 revision: a345530fe50a3f63499029cb17c7887626b288ba
// 규칙 일치는 해당 문자열 조합이 파일/메모리에 있다는 단서일 뿐 기능 ON,
// 플레이어 부정행위, 차단 근거를 단독으로 확정하지 않는다.
// 여러 문자열을 함께 요구해 흔한 API 이름 하나만으로 매치되지 않게 한다.

// 게임 안에 로드되는 첫 Auto Paint bridge의 진단 문구 조합.
rule MECCHA_Repo_AutoPaint_Bridge
{
    meta:
        score = 3
        test_only = false
        family = "auto-paint"
        scope = "game_process_memory_or_bridge_file"
        source_path = "modules/auto-paint*/**/bridge.cpp"
        description = "Known Auto Paint bridge diagnostic-string combination"
        policy = "presence_only_not_activity"
    strings:
        $dispatcher = "mesh-first paint requires the async queued dispatcher" ascii
        $completed = "mesh-first paint completed" ascii
        $stream = "mesh-first single-stroke ServerPaintBatch stream prepared" ascii
    condition:
        all of them
}
// PR-ready Auto Paint 묶음의 특정 DLL 빌드를 위한 보조 규칙.
// 아카이브 SHA-256 8d4dbb71cbf676de21e0a3de1fe5fc9b77aab150fe23c5e6efbe792fafd1ad7b
// 안의 Scripts/native/runtime-bridge.dll에서 문자열을 확인했다.
// ascii와 UTF-16LE(wide)로 저장된 문자열을 모두 요구한다.
rule MECCHA_PRReady_AutoPaint_Bridge
{
    meta:
        score = 3
        test_only = false
        family = "auto-paint"
        scope = "game_process_memory_or_bridge_file"
        source_path = "modules/auto-paint/Scripts/native/runtime-bridge.dll"
        source_archive_sha256 = "8d4dbb71cbf676de21e0a3de1fe5fc9b77aab150fe23c5e6efbe792fafd1ad7b"
        sample_sha256 = "e223ff077d4cf62fa635ec86bc7ec46f1f601ce8e8be1d1c629360364a905078"
        description = "PR-ready Auto Paint bridge pipeline and paint-call combination"
        policy = "presence_only_not_activity"
    strings:
        $pipeline = "mesh_first_pipeline" ascii
        $uv_marker = "mesh-first-uv-color-" wide
        $compact_batch = "ServerCompactPaintBatch" ascii
        $local_brush = "PaintAtUVWithBrush" ascii
    condition:
        all of them
}

// 게임 외부의 직접 인젝터 실행 파일/프로세스를 검사할 때 쓰는 규칙.
// 게임 프로세스만 스캔한다면 이 규칙이 안 잡혀도 인젝터 부재를 뜻하지 않는다.
rule MECCHA_Repo_AutoPaint_Direct_Injector
{
    meta:
        score = 3
        test_only = false
        family = "auto-paint"
        scope = "injector_process_memory_or_file"
        source_path = "modules/auto-paint/src/injector/injector.cpp"
        description = "Hardened Auto Paint direct injector result and ABI strings"
        policy = "presence_only_not_activity"
    strings:
        $abi = "direct_injector_abi_ready" ascii
        $hash = "bridge_hash_mismatch" ascii
        $export = "bridge_start_export_not_found" ascii
        $usage = "usage: runtime-injector.exe --direct <pid> <creation-filetime-utc> <expected-exe-path> <bridge-path>" ascii
    condition:
        all of them
}

// Auto Paint v2/GodMode에서 사용한 간단한 LoadLibrary 인젝터의 문구 조합.
rule MECCHA_Repo_Simple_LoadLibrary_Injector
{
    meta:
        score = 3
        test_only = false
        family = "auto-paint-ver2,godmode"
        scope = "injector_process_memory_or_file"
        source_path = "modules/auto-paint-ver2/runtime/src/injector.cpp and modules/godmode/native/godmode-simple-injector.exe"
        description = "Repository-bundled simple LoadLibrary injector console strings"
        policy = "presence_only_not_activity"
    strings:
        $usage = "usage: runtime-injector.exe <process.exe> <bridge.dll>" wide
        $loaded = "injected pid=" wide
        $failure = "LoadLibraryW failed in target process" wide
    condition:
        all of them
}

// 외부 Python 컨트롤러에 남는 고유 앱 이름과 bridge 경로를 함께 확인한다.
// Python 후보 자동 검사를 켠 경우에도 다른 언어·이름의 프로세스는 검사 범위 밖이다.
rule MECCHA_Repo_AutoPaint_Controller
{
    meta:
        score = 3
        test_only = false
        family = "auto-paint"
        scope = "controller_process_memory_or_file"
        source_path = "modules/auto-paint*/Scripts/*.py and modules/auto-paint-ver2/app/*.py"
        description = "Auto Paint Python controller identity plus private bridge route"
        policy = "presence_only_not_activity"
    strings:
        $app_v1 = "Meccha Auto Paint" ascii wide
        $route = "f10_mesh_first_paint" ascii wide
        $mode = "mesh_first_paint" ascii wide
        $direct_bridge = "meccha-direct-bridge-v1-" ascii wide
        $runtime_v2 = "MecchaCamouflage" ascii wide
        $instance_v2 = "bridge-instance-" ascii wide
    condition:
        all of ($route, $mode) and
        (($app_v1 and $direct_bridge) or ($runtime_v2 and $instance_v2))
}

// 게임 프로세스에 로드되는 GodMode 네이티브 모듈의 고유 진단 문자열.
rule MECCHA_Repo_GodMode_Host_DLL
{
    meta:
        score = 3
        test_only = false
        family = "godmode"
        scope = "game_process_memory_or_dll_file"
        source_path = "modules/godmode/native/godmode_host_402.cpp"
        description = "GodModeHost402 native module diagnostic combination"
        policy = "presence_only_not_activity"
    strings:
        $create = "GodModeHost402 CreateThread failed; win32=" ascii
        $hook = "ProcessEvent hook installed; total=" ascii
        $blocked = "blocked server death call; total=" ascii
        $switch = "GodModeHost402.on" wide
    condition:
        all of them
}

// UE4SS Lua 스크립트 형태의 GodMode가 메모리/파일에 남긴 메시지 조합.
rule MECCHA_Repo_GodMode_UE4SS_Lua
{
    meta:
        score = 3
        test_only = false
        family = "godmode"
        scope = "game_process_memory_or_lua_file"
        source_path = "modules/godmode/Mods/GodMode/Scripts/main.lua"
        description = "Repository GodMode UE4SS script messages and command"
        policy = "presence_only_not_activity"
    strings:
        $log = "[GodMode] %s\\n" ascii
        $protected = "ON - local pawn protected: " ascii
        $usage = "usage: godmode [on|off|status]" ascii
        $loaded = "loaded; F6=toggle, F7=status, or use 'godmode on|off|status'" ascii
    condition:
        all of them
}

// NoClip Lua 스크립트의 로드 알림과 제어 명령을 함께 요구한다.
rule MECCHA_Repo_NoClip_UE4SS_Lua
{
    meta:
        score = 3
        test_only = false
        family = "noclip"
        scope = "game_process_memory_or_lua_file"
        source_path = "modules/noclip/Scripts/main.lua"
        description = "Repository NoClip UE4SS script identity and controls"
        policy = "presence_only_not_activity"
    strings:
        $loaded = "[MecchaNoclip] main.lua loaded" ascii
        $command = "nocliptest" ascii
        $collision = "[MecchaNoclip] Original Collision = " ascii
        $enabled = "[MecchaNoclip] NOCLIP ON" ascii
    condition:
        all of them
}

// 휘파람 변조 DLL의 로그 식별자·경로·대체 에셋 조합.
rule MECCHA_Repo_Whistle_Spoofing_DLL
{
    meta:
        score = 3
        test_only = false
        family = "whistle-spoofing"
        scope = "game_process_memory_or_dll_source"
        source_path = "modules/whistle-spoofing/src/main.cpp"
        description = "Whistle spoofing logger identity, private log path and replacement asset"
        policy = "presence_only_not_activity"
    strings:
        $title = "whistle.dll - ProcessEvent Logger" ascii
        $log_path = "C:/Dumper-7/whistle-pe-log.txt" ascii
        $replacement = "Provoaction_HIKAKIN" ascii
    condition:
        all of them
}

// 바이너리 샘플이 없는 Hide Anywhere는 소스/디버그 흔적에만 적용 가능하다.
// 실게임 메모리에서 이 규칙이 안 잡힌다고 기능 부재를 주장하면 안 된다.
rule MECCHA_Repo_Hide_Anywhere_Source
{
    meta:
        score = 3
        test_only = false
        family = "hide-anywhere"
        scope = "source_or_debug_artifact_only"
        source_path = "modules/hide-anywhere/hide_anywhere.cpp"
        description = "Hide Anywhere source/debug artifact signature; no binary is committed"
        policy = "presence_only_not_activity"
    strings:
        $namespace = "namespace features::hide_anywhere" ascii
        $filled = "offsets::cleon_survivor::FilledValue" ascii
        $view = "IsInViewCheckLate" ascii
        $distance = "EnableDistanceGimmick" ascii
    condition:
        all of them
}

// 게임 메모리 밖에서 실행하는 Python 에임봇의 UI·추적 스키마 문구.
rule MECCHA_Repo_Aimbot_Python
{
    meta:
        score = 3
        test_only = false
        family = "aimbot"
        scope = "external_python_process_or_file"
        source_path = "modules/aimbot/step9_aim_trace.py"
        description = "Repository aimbot controller messages and trace schema"
        policy = "presence_only_not_activity"
    strings:
        $activation = "Activation: hold F8; release it to stop writes" ascii
        $speed = "Synthetic maximum angular speed (deg/s)" ascii
        $settings = "Verification settings: radius=" ascii
        $readback = "readback_error_pitch" ascii
    condition:
        all of them
}

// 외부 Python ESP의 화면 문구·워커 이름을 함께 확인하는 규칙.
rule MECCHA_Repo_ESP_Python
{
    meta:
        score = 3
        test_only = false
        family = "esp"
        scope = "external_python_process_or_file"
        source_path = "modules/esp/esp.py"
        description = "Repository ESP UI and worker identity combination"
        policy = "presence_only_not_activity"
    strings:
        $brand = "MECCHA // VISION" ascii
        $caption = "Pause every visual layer without changing its settings" ascii
        $reader = "MecchaESPReader" ascii
        $skeleton = "MecchaESPSkeleton" ascii
        $failure = "Start the game, wait until the lobby has loaded, then run esp.py again." ascii
    condition:
        all of them
}
