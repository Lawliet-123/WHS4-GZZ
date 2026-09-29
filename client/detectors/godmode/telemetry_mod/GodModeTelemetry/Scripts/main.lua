-- MECCHA CHAMELEON 4.0.2
-- GodMode Anti-Cheat Telemetry Sensor
--
-- IMPORTANT:
-- 이 파일은 플레이어 상태를 변경하지 않는다.
-- 게임 상태와 이벤트를 읽어서 Python Anti-Cheat에 전달한다.

local UEHelpers = require("UEHelpers")

local SAMPLE_INTERVAL_MS = 250
local HOOK_RETRY_INTERVAL_MS = 1000

local elapsed_ms = 0

local output_path =
    [[C:\Users\LG\Desktop\MECCHA-GodMode-AntiCheat\logs\meccha_telemetry.jsonl]]


------------------------------------------------------------
-- Logging
------------------------------------------------------------

local function log(message)
    print(
        string.format(
            "[GodModeTelemetry] %s\n",
            message
        )
    )
end


------------------------------------------------------------
-- UObject helpers
------------------------------------------------------------

local function valid(object)
    if object == nil then
        return false
    end

    local ok, result = pcall(function()
        return object:IsValid()
    end)

    return ok and result
end


local function unwrap(param)
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


local function object_address(object)
    if not valid(object) then
        return 0
    end

    local ok, address = pcall(function()
        return object:GetAddress()
    end)

    if not ok then
        return 0
    end

    return address or 0
end


------------------------------------------------------------
-- Local player
------------------------------------------------------------

local function get_local_pawn()
    local ok, controller = pcall(function()
        return UEHelpers:GetPlayerController()
    end)

    if not ok or not valid(controller) then
        return nil
    end

    local pawn_ok, pawn = pcall(function()
        return controller.Pawn
    end)

    if not pawn_ok or not valid(pawn) then
        return nil
    end

    return pawn
end


local function is_local_pawn(object)
    object = unwrap(object)

    if not valid(object) then
        return false
    end

    local pawn = get_local_pawn()

    if not valid(pawn) then
        return false
    end

    return (
        object_address(object)
        ==
        object_address(pawn)
    )
end


------------------------------------------------------------
-- Event state
------------------------------------------------------------

local events = {
    kill_event = false,
    death_event = false,
    heal_event = false,
    respawn_event = false
}

local last_pawn_address = 0
local saw_death = false


local function reset_one_shot_events()
    events.kill_event = false
    events.death_event = false
    events.heal_event = false
    events.respawn_event = false
end


------------------------------------------------------------
-- Unreal event callbacks
------------------------------------------------------------

local function on_kill_player(
    context,
    target_param,
    source_player_state
)
    local target = unwrap(target_param)

    if is_local_pawn(target) then
        events.kill_event = true

        log(
            "KillPlayer event detected for local player"
        )
    end
end


local function on_death_player(context, ...)
    local player = unwrap(context)

    if is_local_pawn(player) then
        events.death_event = true
        saw_death = true

        log(
            "DeathPlayer event detected"
        )
    end
end


local function on_death_event(context, ...)
    local player = unwrap(context)

    if is_local_pawn(player) then
        events.death_event = true
        saw_death = true

        log(
            "DeathEvent detected"
        )
    end
end


local function on_auto_heal(context, ...)
    local player = unwrap(context)

    if is_local_pawn(player) then
        events.heal_event = true

        log(
            "AutoHeal event detected"
        )
    end
end


------------------------------------------------------------
-- Hook management
------------------------------------------------------------

local registered_hooks = {}


local hook_specs = {
    {
        key = "hunter_kill",
        function_name =
            "BP_FirstPersonCharacter_cLeon_Character_Hunter."
            .. "BP_FirstPersonCharacter_cLeon_Character_Hunter_C."
            .. "KillPlayer",

        callback = on_kill_player
    },

    {
        key = "gamemode_kill",
        function_name =
            "BP_GameMode_cLeon."
            .. "BP_GameMode_cLeon_C."
            .. "KillPlayer",

        callback = on_kill_player
    },

    {
        key = "death_player",
        function_name =
            "BP_FirstPersonCharacter_cLeon_Character."
            .. "BP_FirstPersonCharacter_cLeon_Character_C."
            .. "DeathPlayer",

        callback = on_death_player
    },

    {
        key = "death_event",
        function_name =
            "BP_FirstPersonCharacter_Main."
            .. "BP_FirstPersonCharacter_Main_C."
            .. "DeathEvent",

        callback = on_death_event
    },

    {
        key = "auto_heal",
        function_name =
            "BP_FirstPersonCharacter_Main."
            .. "BP_FirstPersonCharacter_Main_C."
            .. "AutoHeal",

        callback = on_auto_heal
    },

    {
        key = "auto_heal_start",
        function_name =
            "BP_FirstPersonCharacter_Main."
            .. "BP_FirstPersonCharacter_Main_C."
            .. "AutoHealStart",

        callback = on_auto_heal
    }
}


local function resolve_function_name(function_name)
    local ok, object = pcall(function()
        return StaticFindObject(
            function_name
        )
    end)

    if not ok or not valid(object) then
        return nil
    end

    local name_ok, full_name = pcall(function()
        return object:GetFullName()
    end)

    if not name_ok
        or type(full_name) ~= "string"
    then
        return function_name
    end

    -- RegisterHook에서는 "Function " prefix가 필요하지 않음.
    full_name = full_name:gsub(
        "^Function%s+",
        ""
    )

    return full_name
end


local function try_register_hook(spec)
    if registered_hooks[spec.key] then
        return
    end

    local resolved_name =
        resolve_function_name(
            spec.function_name
        )

    if resolved_name == nil then
        return
    end

    local ok, pre_id, post_id = pcall(
        RegisterHook,
        resolved_name,
        spec.callback
    )

    if not ok then
        return
    end

    registered_hooks[spec.key] = {
        function_name = resolved_name,
        pre_id = pre_id,
        post_id = post_id
    }

    log(
        "Hook registered: "
        .. spec.key
        .. " -> "
        .. resolved_name
    )
end


local function try_register_all_hooks()
    for _, spec in ipairs(hook_specs) do
        try_register_hook(spec)
    end
end


------------------------------------------------------------
-- Player state
------------------------------------------------------------

local function read_player_state(pawn)
    local ok, state = pcall(function()
        return {
            health =
                tonumber(pawn.Health) or 0.0,

            max_health =
                tonumber(
                    pawn.MaxHealthValue
                ) or 0.0,

            dead =
                pawn.Dead == true,

            invincible =
                pawn.Invincible == true,

            change_before_health =
                tonumber(
                    pawn.ChangeBeforeHealth
                ) or 0.0
        }
    end)

    if not ok then
        return nil
    end

    return state
end


------------------------------------------------------------
-- JSON helpers
------------------------------------------------------------

local function bool_to_json(value)
    if value == true then
        return "true"
    end

    return "false"
end


------------------------------------------------------------
-- Telemetry output
------------------------------------------------------------

local function write_snapshot(
    state,
    pawn_address
)
    local file = io.open(
        output_path,
        "a"
    )

    if file == nil then
        log(
            "Failed to open telemetry file: "
            .. output_path
        )

        return false
    end

    local timestamp =
        elapsed_ms / 1000.0

    local json_line = string.format(
        '{"timestamp":%.3f,'
        .. '"health":%.6f,'
        .. '"max_health":%.6f,'
        .. '"dead":%s,'
        .. '"invincible":%s,'
        .. '"change_before_health":%.6f,'
        .. '"kill_event":%s,'
        .. '"death_event":%s,'
        .. '"heal_event":%s,'
        .. '"respawn_event":%s,'
        .. '"pawn_address":"0x%X"}',

        timestamp,

        state.health,
        state.max_health,

        bool_to_json(
            state.dead
        ),

        bool_to_json(
            state.invincible
        ),

        state.change_before_health,

        bool_to_json(
            events.kill_event
        ),

        bool_to_json(
            events.death_event
        ),

        bool_to_json(
            events.heal_event
        ),

        bool_to_json(
            events.respawn_event
        ),

        pawn_address
    )

    file:write(
        json_line
    )

    file:write(
        "\n"
    )

    file:flush()
    file:close()

    return true
end


------------------------------------------------------------
-- State sampling loop
------------------------------------------------------------

LoopAsync(
    SAMPLE_INTERVAL_MS,

    function()
        elapsed_ms =
            elapsed_ms
            + SAMPLE_INTERVAL_MS

        local pawn =
            get_local_pawn()

        if pawn == nil then
            return false
        end

        local current_address =
            object_address(pawn)

        ----------------------------------------------------
        -- Pawn replacement / respawn detection
        ----------------------------------------------------

        if (
            last_pawn_address ~= 0
            and current_address ~= 0
            and current_address ~= last_pawn_address
        ) then

            if saw_death then
                events.respawn_event = true

                log(
                    "Pawn changed after death "
                    .. "- respawn detected"
                )
            end

            saw_death = false
        end

        last_pawn_address =
            current_address

        ----------------------------------------------------
        -- Read state
        ----------------------------------------------------

        local state =
            read_player_state(pawn)

        if state == nil then
            return false
        end

        if state.dead then
            saw_death = true
        end

        ----------------------------------------------------
        -- Write telemetry
        ----------------------------------------------------

        local written =
            write_snapshot(
                state,
                current_address
            )

        if written then
            reset_one_shot_events()
        end

        return false
    end
)


------------------------------------------------------------
-- Hook registration retry
------------------------------------------------------------

LoopAsync(
    HOOK_RETRY_INTERVAL_MS,

    function()
        try_register_all_hooks()

        return false
    end
)


------------------------------------------------------------
-- Startup
------------------------------------------------------------

log(
    "Telemetry sensor loaded"
)

log(
    "Output: "
    .. output_path
)

log(
    "Waiting for player and UFunctions..."
)