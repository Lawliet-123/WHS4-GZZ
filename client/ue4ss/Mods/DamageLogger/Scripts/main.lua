local UEHelpers = require("UEHelpers")
local GetKismetSystemLibrary = UEHelpers.GetKismetSystemLibrary
local GetGameplayStatics = UEHelpers.GetGameplayStatics

-- 팀원 PC마다 Steam 설치 위치가 다르면 이 경로만 바꾸면 됨
local LOG_PATH = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\MECCHA CHAMELEON\\Chameleon\\Binaries\\Win64\\ue4ss\\Mods\\DamageLogger\\meccha_aim_telemetry.jsonl"

-- aimbot_detector.py의 MecchaAimTelemetrySensor가 읽는 파일명과 맞춰둠.
-- (Python 쪽 LOG_PATH = Path("logs/meccha_aim_telemetry.jsonl")와 실제로는
--  두 프로그램이 같은 파일을 봐야 하니, 나중에 팀 공유 시 경로를 통일해야 함)

local SESSION_ID = "session_" .. os.date("%Y%m%d_%H%M%S")
-- os.clock()은 실제 경과시간이 아니라 프로세스 CPU 시간이므로 명중 간격 측정에
-- 사용하지 않는다. 이 게임의 Object Dump에서 확인한 GameplayStatics 시간 API를
-- 우선 사용하고, 실제 호출 가능한 첫 시간원을 세션 동안 고정해서 쓴다.
local SESSION_START_PRECISE_SECONDS = nil
local SESSION_START_UNIX_SECONDS = os.time()
local timestampReader = nil
local timestampSource = nil
local timestampFallbackWarned = false
local sample_id = 0

-- 조준 표본은 파일에 매 프레임 기록하지 않는다. Hunter일 때만 약 30Hz로
-- 메모리에 보관했다가, 실제 발사 시 직전 0.5초를 shot_attempt에 붙인다.
-- 이 값들은 덤프에서 확인한 PlayerController.ControlRotation과
-- PlayerController.GetInputMouseDelta를 사용한다.
local AIM_SAMPLE_INTERVAL_MS = 33
-- F8 핵이 목표에 붙은 뒤 바로 쏘지 않고 잠시 유지할 수 있다. 550ms로는
-- "회전 완료 상태"만 남아 실제 자동 수렴 구간을 놓쳤으므로, 발사 때만 JSONL에
-- 기록하는 구조를 유지하면서 버퍼 길이를 5초로 늘린다.
local AIM_TRACE_WINDOW_MS = 5000
local aimTraceBuffer = {}
local aimTelemetryReadyPrinted = false
local aimTelemetryWarningPrinted = false

print("[DamageLogger] loaded")
print("[DamageLogger] Session: " .. SESSION_ID)
print("[DamageLogger] Log path: " .. LOG_PATH)


-- =========================
-- 로그 파일 새로 생성 (JSONL이라 헤더 없음, 그냥 빈 파일로 시작)
-- =========================

local function CreateLogFile()
    local file = io.open(LOG_PATH, "w")
    if not file then
        print("[DamageLogger] Failed to create log file at: " .. LOG_PATH)
        return
    end
    file:close()
    print("[DamageLogger] Log file created successfully")
end


-- =========================
-- 안전하게 위치 얻기
-- =========================

local function SafeGetLocationRaw(actor)
    if not actor or not actor:IsValid() then
        return nil
    end
    local ok, loc = pcall(function() return actor:K2_GetActorLocation() end)
    if not ok or not loc then
        return nil
    end
    return loc
end

local function SafeGetLocation(actor)
    local loc = SafeGetLocationRaw(actor)
    if not loc then
        return 0.0, 0.0, 0.0
    end
    return loc.X, loc.Y, loc.Z
end

local function SafeGetName(actor)
    if not actor or not actor:IsValid() then
        return "unknown"
    end
    local ok, n = pcall(function() return actor:GetFullName() end)
    if ok and n then return n end
    return "unknown"
end

-- JSON 문자열 안에 들어갈 값에서 최소한의 위험 문자만 제거
local function JsonSafeString(s)
    return tostring(s):gsub('"', "'")
end

local function ReadRealTimeSeconds()
    local world = UEHelpers.GetWorld()
    local gameplayStatics = GetGameplayStatics()
    if not world or not world:IsValid() or not gameplayStatics or not gameplayStatics:IsValid() then
        return nil
    end
    local ok, nowSeconds = pcall(function()
        return gameplayStatics:GetRealTimeSeconds(world)
    end)
    if ok and type(nowSeconds) == "number" then
        return nowSeconds
    end
    return nil
end

local function ReadGameTimeSeconds()
    local world = UEHelpers.GetWorld()
    local kismet = GetKismetSystemLibrary()
    if not world or not world:IsValid() or not kismet or not kismet:IsValid() then
        return nil
    end
    local ok, nowSeconds = pcall(function()
        return kismet:GetGameTimeInSeconds(world)
    end)
    if ok and type(nowSeconds) == "number" then
        return nowSeconds
    end
    return nil
end

local TIMESTAMP_SOURCES = {
    { label = "GameplayStatics.GetRealTimeSeconds", read = ReadRealTimeSeconds },
    { label = "KismetSystemLibrary.GetGameTimeInSeconds", read = ReadGameTimeSeconds },
}

local function GetTimestampMs()
    if timestampReader then
        local nowSeconds = timestampReader()
        if type(nowSeconds) == "number" then
            return math.floor((nowSeconds - SESSION_START_PRECISE_SECONDS) * 1000 + 0.5), timestampSource
        end
        print("[DamageLogger] WARNING: precise timestamp source became unavailable: " .. timestampSource)
        timestampReader = nil
        timestampSource = nil
        SESSION_START_PRECISE_SECONDS = nil
    end

    for _, candidate in ipairs(TIMESTAMP_SOURCES) do
        local nowSeconds = candidate.read()
        if type(nowSeconds) == "number" then
            timestampReader = candidate.read
            timestampSource = candidate.label
            SESSION_START_PRECISE_SECONDS = nowSeconds
            print("[DamageLogger] Timestamp source: " .. timestampSource)
            return 0, timestampSource
        end
    end

    -- 두 게임 시간 API를 모두 못 쓴 경우에만 1초 해상도로 폴백한다.
    if not timestampFallbackWarned then
        timestampFallbackWarned = true
        print("[DamageLogger] WARNING: no precise timestamp API available; timestamp resolution is 1 second")
    end
    return (os.time() - SESSION_START_UNIX_SECONDS) * 1000, "os.time(fallback)"
end


-- =========================
-- 라운드 식별자
-- =========================
-- BP_GameState_cLeon.CurrentGamePhase는 덤프에서 확인한 게임 상태 값이다.
-- 대기실→게임→결과 전환을 1초마다 관찰해, 다음 게임의 발사가 이전 게임의
-- 성공률 표본에 섞이지 않도록 round_epoch를 증가시킨다.
-- CurrentGamePhase는 이 experimental UE4SS에서 TrivialObject로만 노출되고
-- 값 접근 API가 확인되지 않았다. 대신 GameState의 SetTimerNumber를 관찰한다.
-- 이 함수는 탐색시간뿐 아니라 8초·15초 같은 전환 타이머에도 호출되므로,
-- 작은 값으로의 상승만으로는 라운드 경계를 만들면 안 된다.
local ENABLE_ROUND_PHASE_PROBE = false
local roundEpoch = 0
local lastGamePhase = nil
local roundStateWarningPrinted = false
local roundStateProbePrinted = false
local lastSearchTimerNumber = nil
local searchTimerHookRegistered = false
local searchTimerWaitLogged = false
-- 호스트가 매 판 탐색시간을 바꿀 수 있으므로 이전 판의 시간과 같을 필요는 없다.
-- 현재 실측된 8초/15초 전환 타이머를 피하기 위해, 0 -> 130초 이상으로 돌아오는
-- 경우만 새 탐색 라운드로 처리한다. 탐색시간을 이보다 짧게 쓸 경우 이 값을 조정한다.
local MIN_SEARCH_DURATION_SECONDS = 130

local function RefreshRoundState()
    if not ENABLE_ROUND_PHASE_PROBE then
        return
    end
    local phase = nil
    local phaseError = nil
    local phasePropertyText = nil
    local phaseValueText = nil
    local phaseValueOk = nil
    local outerOk, outerError = pcall(function()
        local world = UEHelpers.GetWorld()
        local gameState = world and world.GameState
        if gameState and gameState:IsValid() then
            -- CurrentGamePhase 자체는 매 읽기마다 새 TrivialObject 래퍼를 돌려준다.
            -- 래퍼의 tostring 주소가 아니라 실제 byte 값을 비교해야 한다.
            local phaseProperty = gameState.CurrentGamePhase
            phasePropertyText = tostring(phaseProperty)
            local valueOk, phaseValue = pcall(function()
                return phaseProperty:Get()
            end)
            phaseValueOk = valueOk
            phaseValueText = tostring(phaseValue)
            if valueOk then
                phase = phaseValue
            else
                phaseError = phaseValue
            end
        end
    end)
    if phase == nil then
        if not roundStateWarningPrinted then
            roundStateWarningPrinted = true
            print("[DamageLogger] Round state unavailable: " .. tostring(phaseError or outerError or "GameState/CurrentGamePhase not ready"))
        end
        if phasePropertyText and not roundStateProbePrinted then
            roundStateProbePrinted = true
            print(string.format("[DamageLogger] Round phase probe: property=%s get_ok=%s get_value=%s", phasePropertyText, tostring(phaseValueOk), phaseValueText or "nil"))
        end
        return
    end
    phase = tostring(phase)
    if lastGamePhase == nil then
        print("[DamageLogger] Game phase initialized: " .. phase)
    end
    if lastGamePhase ~= nil and phase ~= lastGamePhase then
        roundEpoch = roundEpoch + 1
        print(string.format("[DamageLogger] Game phase changed %s -> %s (round epoch %d)", lastGamePhase, phase, roundEpoch))
    end
    lastGamePhase = phase
    roundStateWarningPrinted = false
    roundStateProbePrinted = false
end

local function GetRoundId()
    if not searchTimerHookRegistered or roundEpoch == 0 then
        return nil
    end
    return string.format("%s:round_%d", SESSION_ID, roundEpoch)
end

LoopAsync(1000, function()
    if not ENABLE_ROUND_PHASE_PROBE then
        return true
    end
    RefreshRoundState()
    return false
end)

local function TryRegisterSearchTimerHook()
    if searchTimerHookRegistered then
        return
    end
    local ok, err = pcall(function()
        RegisterHook(
            "/Game/BluePrints/cLeon/BP_GameState_cLeon.BP_GameState_cLeon_C:SetTimerNumber",
            function(Context, Number)
                local callbackOk, callbackErr = pcall(function()
                    local timerNumber = tonumber(Number:get())
                    if timerNumber == nil then
                        return
                    end
                    if lastSearchTimerNumber == nil then
                        print("[DamageLogger] Search timer initialized: " .. tostring(timerNumber))
                    elseif lastSearchTimerNumber <= 1 and timerNumber >= MIN_SEARCH_DURATION_SECONDS then
                        roundEpoch = roundEpoch + 1
                        print(string.format(
                            "[DamageLogger] Search timer reset 0 -> %d; round started (epoch %d)",
                            timerNumber, roundEpoch
                        ))
                    end
                    lastSearchTimerNumber = timerNumber
                end)
                if not callbackOk then
                    print("[DamageLogger] Search timer callback error: " .. tostring(callbackErr))
                end
            end
        )
    end)
    if ok then
        searchTimerHookRegistered = true
        print("[DamageLogger] Search timer hook registered: SetTimerNumber")
    else
        if not searchTimerWaitLogged then
            searchTimerWaitLogged = true
            print("[DamageLogger] Search timer hook waiting: " .. tostring(err))
        end
    end
end

LoopAsync(2000, function()
    if searchTimerHookRegistered then
        return true
    end
    TryRegisterSearchTimerHook()
    return false
end)


-- =========================
-- 주변 다른 플레이어 위치 수집 (신호 7번: 거리우선 표적전환용)
--
-- World -> GameState -> LiveSurvivors_PlayerState 순회.
-- BP_GameState_cLeon에 실제로 있는 LiveSurvivors_PlayerState는 아직 숨은
-- 술래로 남아 있는 플레이어만 담는다. 이미 전환된 Hunter나 관전자는 후보가 아니다.
-- excludeIds에 들어있는 이름(공격자·피해자)은 후보에서 제외.
-- =========================

-- 후보는 "현재 살아 있는 Survivor"만 남긴다.
local function IsLiveSurvivorPawn(pawn)
    local name = SafeGetName(pawn)
    if name == "unknown" then
        return false
    end
    if string.find(name, "BP_SpectatePawn", 1, true) then
        return false
    end
    if not string.find(name, "BP_FirstPersonCharacter_cLeon_Character_", 1, true) then
        return false
    end
    return string.find(name, "Survivor", 1, true) ~= nil
end

local function GetOtherPlayerPositions(excludeIds)
    local candidates = {}

    local ok, err = pcall(function()
        local world = UEHelpers.GetWorld()
        if not world or not world:IsValid() then return end

        local gameState = world.GameState
        if not gameState or not gameState:IsValid() then return end

        local playerArray = gameState.LiveSurvivors_PlayerState
        if not playerArray then return end

        for i = 1, #playerArray do
            local playerState = playerArray[i]
            if playerState and playerState:IsValid() then
                -- UE4SS의 기본 UEHelpers.GetAllPlayers()도 APlayerState에서
                -- PawnPrivate를 읽는다. 게임별 차이를 고려해 GetPawn()도 폴백한다.
                local pawn = nil
                local privateOk, privatePawn = pcall(function()
                    return playerState.PawnPrivate
                end)
                if privateOk then
                    pawn = privatePawn
                end
                if not pawn or not pawn:IsValid() then
                    local pawnOk, pawnFromFunction = pcall(function()
                        return playerState:GetPawn()
                    end)
                    if pawnOk then
                        pawn = pawnFromFunction
                    end
                end
                if pawn and pawn:IsValid() and IsLiveSurvivorPawn(pawn) then
                    local name = SafeGetName(pawn)
                    if not excludeIds[name] then
                        local loc = SafeGetLocationRaw(pawn)
                        -- 위치를 못 읽은 후보를 (0,0,0)으로 넣으면 최근접 판정이
                        -- 오염되므로, 확인 가능한 후보만 기록한다.
                        if loc then
                            table.insert(candidates, {
                                id = name, x = loc.X, y = loc.Y, z = loc.Z,
                                actor = pawn, location = loc,
                            })
                        end
                    end
                end
            end
        end
    end)

    if not ok then
        print("[DamageLogger] GetOtherPlayerPositions error (candidates left empty): " .. tostring(err))
    end

    return candidates
end


-- =========================
-- 조준 궤적 수집 (신호 5 강화)
--
-- PlayerController.ControlRotation은 실제 조준 방향이고, LiveSurvivors
-- 목록은 이 시점에 살아 있는 술래만 담는다. 따라서 "사격 직전 시점의
-- 조준 방향 ↔ 술래 위치"를 정확히 비교할 수 있다.
-- =========================

local function SafeGetControlRotation(controller)
    local ok, rotation = pcall(function()
        return controller:GetControlRotation()
    end)
    if not ok or not rotation then
        return nil
    end
    local pitchOk, pitch = pcall(function() return rotation.Pitch end)
    local yawOk, yaw = pcall(function() return rotation.Yaw end)
    if not pitchOk or not yawOk or type(pitch) ~= "number" or type(yaw) ~= "number" then
        return nil
    end
    return pitch, yaw
end

local function SafeGetCameraLocation(controller, fallbackPawn)
    local ok, location = pcall(function()
        local camera = controller.PlayerCameraManager
        return camera.CameraCachePrivate.POV.Location
    end)
    if ok and location and type(location.X) == "number" then
        return location
    end
    -- CameraCache 접근이 게임 버전에서 막히더라도 수평 조준 판정은 Pawn 위치로
    -- 계속 가능하다. 이 경우 수직 오차는 이후 실전 로그에서 넓게 보정한다.
    return SafeGetLocationRaw(fallbackPawn)
end

local function BuildAimTraceJson()
    local samples = {}
    for _, sample in ipairs(aimTraceBuffer) do
        local candidateParts = {}
        for candidateId, location in pairs(sample.candidates) do
            table.insert(candidateParts, string.format(
                '"%s":[%.2f,%.2f,%.2f]',
                JsonSafeString(candidateId), location.x, location.y, location.z
            ))
        end
        table.insert(samples, string.format(
            '{"timestamp_ms":%d,"view_pos":[%.2f,%.2f,%.2f],"control_rotation":[%.4f,%.4f],"candidates":{%s}}',
            sample.timestampMs,
            sample.viewPos.X, sample.viewPos.Y, sample.viewPos.Z,
            sample.pitch, sample.yaw,
            table.concat(candidateParts, ",")
        ))
    end
    return "[" .. table.concat(samples, ",") .. "]"
end

LoopAsync(AIM_SAMPLE_INTERVAL_MS, function()
    local controller = UEHelpers.GetPlayerController()
    if not controller or not controller:IsValid() then
        return false
    end

    local pawn = nil
    local pawnOk, acknowledgedPawn = pcall(function() return controller.AcknowledgedPawn end)
    if pawnOk then pawn = acknowledgedPawn end
    if not pawn or not pawn:IsValid() or not string.find(SafeGetName(pawn), "Hunter", 1, true) then
        -- Survivor/대기실 표본이 다음 Hunter 발사에 섞이지 않도록 비운다.
        aimTraceBuffer = {}
        return false
    end

    local pitch, yaw = SafeGetControlRotation(controller)
    local viewPos = SafeGetCameraLocation(controller, pawn)
    if pitch == nil or yaw == nil or not viewPos then
        if not aimTelemetryWarningPrinted then
            aimTelemetryWarningPrinted = true
            print("[DamageLogger] AIM_TELEMETRY waiting: ControlRotation/CameraCache unavailable")
        end
        return false
    end

    local candidateMap = {}
    for _, candidate in ipairs(GetOtherPlayerPositions({})) do
        candidateMap[candidate.id] = candidate
    end
    local timestampMs = GetTimestampMs()
    table.insert(aimTraceBuffer, {
        timestampMs = timestampMs,
        viewPos = viewPos,
        pitch = pitch,
        yaw = yaw,
        candidates = candidateMap,
    })
    while #aimTraceBuffer > 0 and timestampMs - aimTraceBuffer[1].timestampMs > AIM_TRACE_WINDOW_MS do
        table.remove(aimTraceBuffer, 1)
    end

    if not aimTelemetryReadyPrinted then
        aimTelemetryReadyPrinted = true
        print("[DamageLogger] AIM_TELEMETRY active: ControlRotation trace buffer started")
    end
    return false
end)


-- =========================
-- 비가시 추적(신호 12) 판정 — 노클립 로거의 CheckBlockedPath와 같은 기법
--
-- 파이썬(aimbot_detector.py) 쪽에는 벽/지형 데이터가 전혀 없어서
-- 좌표만으로는 "시야가 막혔는지"를 절대 계산할 수 없다 (구조적으로 불가능한
-- 계산을 스텁으로 미뤄뒀던 부분). 라인트레이스는 게임 안(Lua)에서만 할 수
-- 있으므로, 명중 순간 여기서 직접 검사해서 결과(los_clear)를 로그에 실어보낸다.
-- =========================

local function CheckLineOfSight(attacker, victim, fromLoc, toLoc)
    if not fromLoc or not toLoc then
        return nil  -- 위치를 못 읽었으면 확인 불가
    end

    local kismet = GetKismetSystemLibrary()
    if not kismet or not kismet:IsValid() then
        return nil
    end

    local TraceColor = { R = 0, G = 0, B = 0, A = 0 }
    -- 공격자와 피해자는 장애물이 아니다. 둘을 제외한 뒤 남는 blocking hit만
    -- 벽/지형에 의한 LOS 차단으로 취급한다.
    local ActorsToIgnore = { attacker, victim }
    local HitResult = {}

    local ok, wasHit = pcall(function()
        return kismet:LineTraceSingle(
            attacker, fromLoc, toLoc,
            0,               -- TraceChannel (노클립 로거와 동일)
            false,
            ActorsToIgnore,
            0,               -- DrawDebugType: 디버그 라인 표시 안 함
            HitResult,
            true,
            TraceColor, TraceColor, 0.0
        )
    end)

    if not ok then
        return nil  -- 트레이스 자체가 에러났으면 확인 불가
    end

    return not wasHit  -- 공격자/피해자를 제외하고 벽에 맞았으면 차단
end

local function NormalizeAngle(angle)
    return (angle + 180.0) % 360.0 - 180.0
end

-- 발사 순간 조준선에 가장 가까운 살아 있는 술래 후보 한 명만 고른다.
-- 모든 후보·모든 프레임을 LineTrace하지 않아 성능 부담을 제한한다.
local function GetBestAlignedCandidate(attacker)
    local controller = UEHelpers.GetPlayerController()
    if not controller or not controller:IsValid() then return nil end

    local pitch, yaw = SafeGetControlRotation(controller)
    local viewPos = SafeGetCameraLocation(controller, attacker)
    if pitch == nil or yaw == nil or not viewPos then return nil end

    local excludeIds = {}
    excludeIds[SafeGetName(attacker)] = true
    local best = nil
    for _, candidate in ipairs(GetOtherPlayerPositions(excludeIds)) do
        local dx = candidate.x - viewPos.X
        local dy = candidate.y - viewPos.Y
        local dz = candidate.z - viewPos.Z
        local horizontal = math.sqrt(dx * dx + dy * dy)
        local targetYaw = math.deg(math.atan(dy, dx))
        local targetPitch = math.deg(math.atan(dz, horizontal))
        local pitchDelta = NormalizeAngle(targetPitch - pitch)
        local yawDelta = NormalizeAngle(targetYaw - yaw)
        local errorDeg = math.sqrt(pitchDelta * pitchDelta + yawDelta * yawDelta)
        if not best or errorDeg < best.errorDeg then
            best = { candidate = candidate, errorDeg = errorDeg, viewPos = viewPos }
        end
    end

    if best then
        best.losClear = CheckLineOfSight(
            attacker, best.candidate.actor, best.viewPos, best.candidate.location
        )
    end
    return best
end


-- =========================
-- 발사 시도와 확정 성공 결과를 JSONL 한 줄씩 기록
-- =========================

local function AppendJsonl(line)
    local file = io.open(LOG_PATH, "a")
    if file then
        file:write(line .. "\n")
        file:close()
        return true
    end
    print("[DamageLogger] WARNING: could not append to log file")
    return false
end

-- SpawnShotEffect(Local)은 실제로 총을 발사한 모든 시도를 남긴다.
-- IsHit은 빈 공간 발사에서도 true가 될 수 있어 JSON이나 판정에는 사용하지 않는다.
local function WriteShotAttemptJsonl(attacker)
    local attackerId = SafeGetName(attacker)
    local ax, ay, az = SafeGetLocation(attacker)
    local timestampMs, usedTimestampSource = GetTimestampMs()
    local roundId = GetRoundId()
    local aimTraceJson = BuildAimTraceJson()
    local aligned = GetBestAlignedCandidate(attacker)

    local aimedCandidateIdJson = "null"
    local aimedCandidateErrorJson = "null"
    local aimedCandidateLosJson = "null"
    if aligned then
        aimedCandidateIdJson = '"' .. JsonSafeString(aligned.candidate.id) .. '"'
        aimedCandidateErrorJson = string.format("%.4f", aligned.errorDeg)
        if aligned.losClear ~= nil then
            aimedCandidateLosJson = aligned.losClear and "true" or "false"
        end
    end

    local roundIdJson = '"' .. JsonSafeString(roundId) .. '"'
    local line = string.format(
        '{"event_type":"shot_attempt","session_id":"%s","round_id":%s,"timestamp_ms":%d,"timestamp_source":"%s","attacker_id":"%s",' ..
        '"attacker_pos":[%.2f,%.2f,%.2f],"aimed_candidate_id":%s,"aimed_candidate_error_deg":%s,' ..
        '"aimed_candidate_los_clear":%s,"aim_trace":%s}',
        SESSION_ID, roundIdJson,
        timestampMs, JsonSafeString(usedTimestampSource), JsonSafeString(attackerId),
        ax, ay, az, aimedCandidateIdJson, aimedCandidateErrorJson, aimedCandidateLosJson, aimTraceJson
    )
    AppendJsonl(line)
    if aligned then
        print(string.format(
            "[DamageLogger] SHOT attempt attacker=%s aim_samples=%d aimed=%s error=%.2f los=%s",
            attackerId, #aimTraceBuffer, aligned.candidate.id, aligned.errorDeg, tostring(aligned.losClear)
        ))
    else
        print(string.format("[DamageLogger] SHOT attempt attacker=%s aim_samples=%d aimed=none", attackerId, #aimTraceBuffer))
    end
end

-- KillPlayer는 숨은 술래를 찾아 탈락/역할전환시킨 확정 결과만 남긴다.
local function WriteConfirmedOutcomeJsonl(attacker, victim, eventSource)
    eventSource = eventSource or "unknown"
    local attackerId = SafeGetName(attacker)
    local victimId = SafeGetName(victim)
    local attackerLoc = SafeGetLocationRaw(attacker)
    local victimLoc = SafeGetLocationRaw(victim)
    local ax, ay, az = SafeGetLocation(attacker)
    local vx, vy, vz = SafeGetLocation(victim)

    local losClear = CheckLineOfSight(attacker, victim, attackerLoc, victimLoc)
    -- JSON에는 true/false/null로 실어보냄 (nil = 확인 불가, null로 직렬화)
    local losClearJson = "null"
    if losClear ~= nil then
        losClearJson = losClear and "true" or "false"
    end

    local excludeIds = {}
    excludeIds[attackerId] = true
    excludeIds[victimId] = true
    local others = GetOtherPlayerPositions(excludeIds)

    local otherParts = {}
    for _, c in ipairs(others) do
        table.insert(otherParts, string.format(
            '"%s":[%.2f,%.2f,%.2f]',
            JsonSafeString(c.id), c.x, c.y, c.z
        ))
    end
    local otherCandidatesJson = "{" .. table.concat(otherParts, ",") .. "}"

    sample_id = sample_id + 1
    local timestampMs, usedTimestampSource = GetTimestampMs()
    local roundId = GetRoundId()

    local roundIdJson = '"' .. JsonSafeString(roundId) .. '"'
    local line = string.format(
        '{"event_type":"confirmed_outcome","session_id":"%s","round_id":%s,"timestamp_ms":%d,"timestamp_source":"%s","attacker_id":"%s","victim_id":"%s","event_source":"%s",' ..
        '"attacker_pos":[%.2f,%.2f,%.2f],"victim_pos":[%.2f,%.2f,%.2f],' ..
        '"los_clear":%s,"other_candidates":%s}',
        SESSION_ID, roundIdJson,
        timestampMs, JsonSafeString(usedTimestampSource),
        JsonSafeString(attackerId), JsonSafeString(victimId), JsonSafeString(eventSource),
        ax, ay, az,
        vx, vy, vz,
        losClearJson,
        otherCandidatesJson
    )

    AppendJsonl(line)

    print(string.format(
        "[DamageLogger] OUTCOME #%d source=%s attacker=%s victim=%s others_seen=%d",
        sample_id, eventSource, attackerId, victimId, #others
    ))
end


-- =========================
-- RedpointDamageNotificationInterface:OnDealtDamageToTarget 후킹
-- 파라미터: (DamageDealt: int, TargetActor: victim), Context = 공격자
-- =========================

local ENABLE_LEGACY_LOCAL_HOOKS = false

if ENABLE_LEGACY_LOCAL_HOOKS then
RegisterHook("/Script/RedpointEOSFrameworkExtra.RedpointDamageNotificationInterface:OnDealtDamageToTarget", function(Context, DamageDealt, TargetActor)
    local ok, err = pcall(function()
        local attacker = Context:get()
        local damage = DamageDealt:get()
        local victim = TargetActor:get()

        WriteConfirmedOutcomeJsonl(attacker, victim, "LegacyDamageHook")
    end)

    if not ok then
        print("[DamageLogger] OnDealtDamageToTarget hook error: " .. tostring(err))
    end
end)


-- =========================
-- OnKilledTargetActor는 참고용 콘솔 출력만 (다른 신호는 팀원 담당 범위)
-- =========================

RegisterHook("/Script/RedpointEOSFrameworkExtra.RedpointDamageNotificationInterface:OnKilledTargetActor", function(Context, SourceAbilitySystem, TargetActor)
    local ok, err = pcall(function()
        local attackerName = SafeGetName(Context:get())
        local victimName = SafeGetName(TargetActor:get())
        print(string.format("[DamageLogger] KILL attacker=%s victim=%s", attackerName, victimName))
    end)

    if not ok then
        print("[DamageLogger] OnKilledTargetActor hook error: " .. tostring(err))
    end
end)
end


-- =========================
-- HitSuccess 후킹 — 이 게임의 총(Hunter) 명중 판정용 커스텀 함수
--
-- Object Dump에서 발견: BP_FirstPersonCharacter_cLeon_Character_Hunter_C:HitSuccess
-- 파라미터가 FirstPersonCharacter(맞은 대상) 딱 하나뿐이라 데미지량은 안 줌.
-- Context(self) = 쏜 사람(Hunter), FirstPersonCharacter = 맞은 대상.
--
-- 주의: 이건 게임 자체 블루프린트 클래스라 모드 로드 시점(게임 켜지는 순간)엔
-- 아직 클래스가 메모리에 없을 수 있음 -> 매치에 들어가서 헌터가 스폰된 뒤에야
-- 등록될 수 있으므로, 성공할 때까지 몇 초마다 재시도한다.
-- =========================

-- HitSuccess는 "맞았다"는 사실만 알 수 있고, 술래 탈락/역할전환까지 확정하지는
-- 않는다. 실제 탐지 데이터는 아래 KillPlayer 훅이 검증된 뒤 그 결과만 기록한다.
local ENABLE_LEGACY_HIT_TELEMETRY = false
local hitSuccessRegistered = false

local function TryRegisterHitSuccessHook()
    if hitSuccessRegistered then
        return
    end

    local ok = pcall(function()
        RegisterHook(
            "/Game/BluePrints/cLeon/BP_FirstPersonCharacter_cLeon_Character_Hunter.BP_FirstPersonCharacter_cLeon_Character_Hunter_C:HitSuccess",
            function(Context, FirstPersonCharacter)
                local hookOk, hookErr = pcall(function()
                    local attacker = Context:get()
                    local victim = FirstPersonCharacter:get()
                    if ENABLE_LEGACY_HIT_TELEMETRY then
                        -- HitSuccess는 확정 결과가 아니므로 실제 JSONL에는 기록하지 않는다.
                        print("[DamageLogger] legacy HitSuccess telemetry is disabled")
                    end
                    print("[DamageLogger] HitSuccess observed (not treated as confirmed outcome)")
                end)
                if not hookOk then
                    print("[DamageLogger] HitSuccess callback error: " .. tostring(hookErr))
                end
            end
        )
    end)

    if ok then
        hitSuccessRegistered = true
        print("[DamageLogger] HitSuccess hook registered (delayed registration succeeded)")
    end
end

-- 2초마다 재시도, 성공하면 루프 종료 (true를 반환하면 LoopAsync가 멈춤)
LoopAsync(2000, function()
    if hitSuccessRegistered then
        return true
    end
    TryRegisterHitSuccessHook()
    return false
end)


-- =========================
-- 게임 규칙 기반 에임봇 신호용 텔레메트리 훅
--
-- SpawnShotEffect(Local)은 발사 시도만 남긴다. IsHit은 실제 성공을 뜻하지 않는다.
-- KillPlayer는 숨은 술래를 찾아 탈락 또는 역할전환시킨 "확정 성공 결과"로 기록한다.
-- =========================

local HUNTER_CLASS_PATH = "/Game/BluePrints/cLeon/BP_FirstPersonCharacter_cLeon_Character_Hunter.BP_FirstPersonCharacter_cLeon_Character_Hunter_C"
local SHOT_EFFECT_LOCAL_PATH = HUNTER_CLASS_PATH .. ":SpawnShotEffect(Local)"
local KILL_PLAYER_PATH = HUNTER_CLASS_PATH .. ":KillPlayer"
local shotEffectProbeRegistered = false
local killPlayerProbeRegistered = false
local shotEffectWaitLogged = false
local killPlayerWaitLogged = false

local function ReadProbeValue(param)
    if param == nil then
        return nil
    end
    local ok, value = pcall(function()
        return param:get()
    end)
    if ok then
        return value
    end
    return param
end

local function TryRegisterOutcomeProbes()
    if not shotEffectProbeRegistered then
        local shotOk, shotErr = pcall(function()
            RegisterHook(SHOT_EFFECT_LOCAL_PATH, function(Context, Endpoint, IsHit, HitRotation, Seed)
                local callbackOk, callbackErr = pcall(function()
                    local shooter = ReadProbeValue(Context)
                    local isHit = ReadProbeValue(IsHit)
                    print(string.format(
                        "[DamageLogger] SHOT_PROBE shooter=%s is_hit=%s",
                        SafeGetName(shooter), tostring(isHit)
                    ))
                    WriteShotAttemptJsonl(shooter)
                end)
                if not callbackOk then
                    print("[DamageLogger] SHOT_PROBE callback error: " .. tostring(callbackErr))
                end
            end)
        end)
        if shotOk then
            shotEffectProbeRegistered = true
            print("[DamageLogger] SHOT_PROBE registered: SpawnShotEffect(Local)")
        else
            if not shotEffectWaitLogged then
                shotEffectWaitLogged = true
                print("[DamageLogger] SHOT_PROBE waiting: " .. tostring(shotErr))
            end
        end
    end

    if not killPlayerProbeRegistered then
        local killOk, killErr = pcall(function()
            RegisterHook(KILL_PLAYER_PATH, function(Context, FirstPersonCharacter, SourcePlayerState)
                local callbackOk, callbackErr = pcall(function()
                    local hunter = ReadProbeValue(Context)
                    local target = ReadProbeValue(FirstPersonCharacter)
                    local sourcePlayerState = ReadProbeValue(SourcePlayerState)
                    print(string.format(
                        "[DamageLogger] OUTCOME_PROBE KillPlayer hunter=%s target=%s source_state=%s",
                        SafeGetName(hunter), SafeGetName(target), SafeGetName(sourcePlayerState)
                    ))
                    WriteConfirmedOutcomeJsonl(hunter, target, "KillPlayer")
                end)
                if not callbackOk then
                    print("[DamageLogger] OUTCOME_PROBE callback error: " .. tostring(callbackErr))
                end
            end)
        end)
        if killOk then
            killPlayerProbeRegistered = true
            print("[DamageLogger] OUTCOME_PROBE registered: KillPlayer")
        else
            if not killPlayerWaitLogged then
                killPlayerWaitLogged = true
                print("[DamageLogger] OUTCOME_PROBE waiting: " .. tostring(killErr))
            end
        end
    end
end

LoopAsync(2000, function()
    if shotEffectProbeRegistered and killPlayerProbeRegistered then
        return true
    end
    TryRegisterOutcomeProbes()
    return false
end)


-- =========================
-- Primary host telemetry: BP_FirstPersonPlayerController_LINK:Damage(Server)
--
-- 직접 생성한 GObjects-Dump-WithProperties.txt에서 확인한 인자 순서:
-- TargetActor, DamageValue, TeamIndex, DamageType, SourceAgentPoint,
-- UnAvoidable, DamageName, HitRezult, SourceActor
--
-- (Server) RPC이므로 호스트에서 모든 플레이어 간 공격을 수집할 후보이다.
-- 실제 공격자/피해자는 Context가 아니라 SourceActor/TargetActor에 들어온다.
-- =========================

local ENABLE_HOST_DAMAGE_PROBES = false
local DAMAGE_SERVER_PATH = "/Game/BluePrints/LINK/BP_FirstPersonPlayerController_LINK.BP_FirstPersonPlayerController_LINK_C:Damage(Server)"
local damageServerRegistered = not ENABLE_HOST_DAMAGE_PROBES

local function GetHookValue(param)
    if param == nil then
        return nil
    end
    local ok, value = pcall(function()
        return param:get()
    end)
    if ok then
        return value
    end
    -- UE4SS가 원시 타입을 wrapper 없이 넘기는 경우를 허용한다.
    return param
end

local function TryRegisterDamageServerHook()
    if damageServerRegistered then
        return
    end

    local ok, err = pcall(function()
        RegisterHook(DAMAGE_SERVER_PATH, function(
            Context, TargetActor, DamageValue, TeamIndex, DamageType,
            SourceAgentPoint, UnAvoidable, DamageName, HitRezult, SourceActor
        )
            local hookOk, hookErr = pcall(function()
                local victim = GetHookValue(TargetActor)
                local attacker = GetHookValue(SourceActor)
                local damage = tonumber(GetHookValue(DamageValue)) or 0

                if not attacker or not victim then
                    print("[DamageLogger] Damage(Server) fired but SourceActor/TargetActor was missing")
                    return
                end

                print(string.format(
                    "[DamageLogger] Damage(Server) fired attacker=%s victim=%s damage=%.1f",
                    SafeGetName(attacker), SafeGetName(victim), damage
                ))
                print("[DamageLogger] Damage(Server) probe observed; JSON output stays disabled during diagnosis")
            end)

            if not hookOk then
                print("[DamageLogger] Damage(Server) callback error: " .. tostring(hookErr))
            end
        end)
    end)

    if ok then
        damageServerRegistered = true
        print("[DamageLogger] Damage(Server) hook registered (host telemetry primary)")
    else
        print("[DamageLogger] Damage(Server) hook waiting: " .. tostring(err))
    end
end

LoopAsync(2000, function()
    if not ENABLE_HOST_DAMAGE_PROBES then
        return true
    end
    if damageServerRegistered then
        return true
    end
    TryRegisterDamageServerHook()
    return false
end)


-- =========================
-- Damage-path probe set
--
-- Damage(Server)가 호출되지 않았으므로, 덤프에서 확인한 인접 경로들을 함께
-- 관찰한다. 이 구간은 어떤 함수가 호스트에서 실제 실행되는지 찾는 용도이며
-- raw telemetry/탐지 점수는 기록하지 않는다.
-- =========================

local probeStates = {}

local function RegisterProbe(label, path, callback)
    if probeStates[label] == "registered" then
        return true
    end

    local ok, err = pcall(function()
        RegisterHook(path, callback)
    end)

    if ok then
        probeStates[label] = "registered"
        print("[DamageLogger] PROBE registered: " .. label)
        return true
    end

    if probeStates[label] == nil then
        probeStates[label] = "waiting"
        print("[DamageLogger] PROBE waiting: " .. label .. " -> " .. tostring(err))
    end
    return false
end

local function LogFullDamageProbe(label, TargetActor, DamageValue, SourceActor)
    local victim = GetHookValue(TargetActor)
    local attacker = GetHookValue(SourceActor)
    local damage = tonumber(GetHookValue(DamageValue)) or 0
    print(string.format(
        "[DamageLogger] PROBE FIRED %s attacker=%s victim=%s damage=%.1f",
        label, SafeGetName(attacker), SafeGetName(victim), damage
    ))
end

local function TryRegisterDamageProbes()
    local allRegistered = true

    allRegistered = RegisterProbe(
        "DamageToPlayerController",
        "/Game/BluePrints/LINK/BP_FirstPersonPlayerController_LINK.BP_FirstPersonPlayerController_LINK_C:DamageToPlayerController",
        function(Context, TargetActor, DamageValue, TeamIndex, DamageType, SourceAgentPoint, UnAvoidable, DamageName, HitRezult, SourceActor)
            local ok, err = pcall(function()
                LogFullDamageProbe("DamageToPlayerController", TargetActor, DamageValue, SourceActor)
            end)
            if not ok then print("[DamageLogger] PROBE callback error DamageToPlayerController: " .. tostring(err)) end
        end
    ) and allRegistered

    allRegistered = RegisterProbe(
        "Damage",
        "/Game/BluePrints/LINK/BP_FirstPersonPlayerController_LINK.BP_FirstPersonPlayerController_LINK_C:Damage",
        function(Context, DamageValue, TeamIndex, DamageType, SourceAgentPoint, UnAvoidable, DamageName, SourceActor, Finish)
            local ok, err = pcall(function()
                local victim = GetHookValue(Context)
                local attacker = GetHookValue(SourceActor)
                local damage = tonumber(GetHookValue(DamageValue)) or 0
                print(string.format(
                    "[DamageLogger] PROBE FIRED Damage attacker=%s victim_context=%s damage=%.1f",
                    SafeGetName(attacker), SafeGetName(victim), damage
                ))
            end)
            if not ok then print("[DamageLogger] PROBE callback error Damage: " .. tostring(err)) end
        end
    ) and allRegistered

    allRegistered = RegisterProbe(
        "SetHealthValue(Server)-LINK",
        "/Game/FirstPerson/Blueprints/BP_FirstPersonCharacter_LINK.BP_FirstPersonCharacter_LINK_C:SetHealthValue(Server)",
        function(Context, TargetValue)
            local ok, err = pcall(function()
                print(string.format(
                    "[DamageLogger] PROBE FIRED SetHealthValue(Server)-LINK victim_context=%s health=%.1f",
                    SafeGetName(GetHookValue(Context)), tonumber(GetHookValue(TargetValue)) or -1
                ))
            end)
            if not ok then print("[DamageLogger] PROBE callback error SetHealthValue(Server)-LINK: " .. tostring(err)) end
        end
    ) and allRegistered

    allRegistered = RegisterProbe(
        "SetHealthValue(Server)-MAIN",
        "/Game/FirstPerson/Blueprints/BP_FirstPersonCharacter_Main.BP_FirstPersonCharacter_Main_C:SetHealthValue(Server)",
        function(Context, TargetValue)
            local ok, err = pcall(function()
                print(string.format(
                    "[DamageLogger] PROBE FIRED SetHealthValue(Server)-MAIN victim_context=%s health=%.1f",
                    SafeGetName(GetHookValue(Context)), tonumber(GetHookValue(TargetValue)) or -1
                ))
            end)
            if not ok then print("[DamageLogger] PROBE callback error SetHealthValue(Server)-MAIN: " .. tostring(err)) end
        end
    ) and allRegistered

    return allRegistered
end

LoopAsync(2000, function()
    if not ENABLE_HOST_DAMAGE_PROBES then
        return true
    end
    return TryRegisterDamageProbes()
end)


CreateLogFile()
