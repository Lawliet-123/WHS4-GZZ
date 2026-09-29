local UEHelpers = require("UEHelpers")

local GetPlayerController = UEHelpers.GetPlayerController
local GetKismetSystemLibrary = UEHelpers.GetKismetSystemLibrary

-- UE4SS cwd is usually the game Binaries\\Win64 directory. Resolve from this script instead.
local source = debug.getinfo(1, "S").source:gsub("^@", "")
local scripts = source:match("^(.*[/\\])")
assert(scripts, "NoclipLogger: cannot resolve Scripts directory")
local LOG_PATH = scripts .. "../noclip_log.csv"
local LOG_INTERVAL = 1000

local elapsed_ms = 0
local sample_id = 0

local previousLocation = nil

print("[NoclipLogger] loaded")


-- =========================
-- CSV 새로 생성
-- =========================

local function CreateLogFile()

    local file = io.open(LOG_PATH, "w")

    if not file then
        print("[NoclipLogger] Failed to create CSV")
        return
    end

    file:write(
        "sample_id,elapsed_ms,x,y,z,collision,blocked_path\n"
    )

    file:close()

    print("[NoclipLogger] CSV created: " .. LOG_PATH)
end


-- =========================
-- 이전 위치 → 현재 위치
-- Line Trace
-- =========================

local function CheckBlockedPath(pawn, startLocation, endLocation)

    if not startLocation then
        return 0
    end

    local kismet = GetKismetSystemLibrary()

    if not kismet or not kismet:IsValid() then
        return -1
    end


    local TraceColor = {
        R = 0,
        G = 0,
        B = 0,
        A = 0
    }

    local TraceHitColor = TraceColor

    -- UE4SS 공식 LineTraceMod와 같은 기본 Trace Channel
    local TraceChannel = 0

    -- Debug Line 표시 안 함
    local DrawDebugType = 0

    local ActorsToIgnore = {}
    local HitResult = {}


    local success, wasHit = pcall(function()

        return kismet:LineTraceSingle(
            pawn,
            startLocation,
            endLocation,
            TraceChannel,
            false,
            ActorsToIgnore,
            DrawDebugType,
            HitResult,
            true,
            TraceColor,
            TraceHitColor,
            0.0
        )

    end)


    if not success then

        print("[NoclipLogger] LineTrace error")

        return -1

    end


    if wasHit then
        return 1
    end

    return 0
end


-- =========================
-- 플레이어 상태 수집
-- =========================

local function LogPlayerState()

    local playerController = GetPlayerController()

    if not playerController then
        return
    end


    local pawn = playerController.Pawn

    if not pawn then
        return
    end


    local location = pawn:K2_GetActorLocation()

    if not location then
        return
    end


    local collision = -1

    if pawn.BodyCapsule then
        collision = pawn.BodyCapsule:GetCollisionEnabled()
    end


    -- 이전 위치와 현재 위치 사이에
    -- Blocking Object가 있었는지 확인
    local blockedPath = CheckBlockedPath(
        pawn,
        previousLocation,
        location
    )


    sample_id = sample_id + 1


    local file = io.open(LOG_PATH, "a")

    if file then

        file:write(string.format(
            "%d,%d,%.2f,%.2f,%.2f,%s,%d\n",
            sample_id,
            elapsed_ms,
            location.X,
            location.Y,
            location.Z,
            tostring(collision),
            blockedPath
        ))

        file:close()

    end


    print(string.format(
        "[NoclipLogger] T=%d X=%.2f Y=%.2f Z=%.2f Collision=%s Blocked=%d",
        elapsed_ms,
        location.X,
        location.Y,
        location.Z,
        tostring(collision),
        blockedPath
    ))


    -- 현재 위치를 다음 검사에서 이전 위치로 사용
    previousLocation = {
        X = location.X,
        Y = location.Y,
        Z = location.Z
    }
end


CreateLogFile()


-- =========================
-- 1초마다 자동 기록
-- =========================

LoopAsync(LOG_INTERVAL, function()

    elapsed_ms = elapsed_ms + LOG_INTERVAL

    ExecuteInGameThread(function()
        LogPlayerState()
    end)

    return false
end)