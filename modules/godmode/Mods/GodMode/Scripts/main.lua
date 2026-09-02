-- Meccha Chameleon 4.0.x / UE4SS local God Mode lab mod.
-- Scope: the locally controlled pawn in an authorized private test session.

local UEHelpers = require("UEHelpers")

local enabled = true
local last_pawn_id = nil
local last_failed_pawn_id = nil
local apply_queued = false
local refresh_ms = 500

local function log(message)
    print(string.format("[GodMode] %s\n", message))
end

local function valid(object)
    if object == nil then return false end
    local ok, result = pcall(function() return object:IsValid() end)
    return ok and result
end

local function get_local_pawn()
    local ok, controller = pcall(function()
        return UEHelpers:GetPlayerController()
    end)
    if not ok or not valid(controller) then return nil end

    local pawn_ok, pawn = pcall(function() return controller.Pawn end)
    if not pawn_ok or not valid(pawn) then return nil end
    return pawn
end

local function is_eligible_local_pawn(pawn)
    local ok, hunter = pcall(function() return pawn.IsHunter end)
    if ok and hunter == true then return false end

    local name_ok, full_name = pcall(function() return pawn:GetFullName() end)
    if name_ok and type(full_name) == "string" then
        if full_name:find("Hunter", 1, true) then return false end
    end

    return true
end

local function apply_god_mode()
    if not enabled then return end

    local pawn = get_local_pawn()
    if pawn == nil or not is_eligible_local_pawn(pawn) then return end

    local name_ok, full_name = pcall(function() return pawn:GetFullName() end)
    local pawn_id = name_ok and tostring(full_name) or "unknown"

    local ok, error_message = pcall(function()
        pawn.Invincible = true
        pawn.Dead = false

        local max_health = pawn.MaxHealthValue
        if type(max_health) == "number" and max_health > 0 then
            pawn.Health = max_health
            pawn.ChangeBeforeHealth = max_health
        end
    end)

    if not ok then
        if last_failed_pawn_id ~= pawn_id then
            last_failed_pawn_id = pawn_id
            log("apply failed: " .. tostring(error_message))
        end
    elseif last_pawn_id ~= pawn_id then
        last_pawn_id = pawn_id
        last_failed_pawn_id = nil
        local read_ok, state = pcall(function()
            return string.format(
                "Invincible=%s Dead=%s Health=%s MaxHealth=%s",
                tostring(pawn.Invincible), tostring(pawn.Dead),
                tostring(pawn.Health), tostring(pawn.MaxHealthValue)
            )
        end)
        log("ON - local pawn protected: " .. pawn_id)
        log(read_ok and state or "state read-back failed")
    end
end

local function set_enabled(value)
    enabled = value
    if not enabled then
        local pawn = get_local_pawn()
        if pawn ~= nil then pcall(function() pawn.Invincible = false end) end
        last_pawn_id = nil
        last_failed_pawn_id = nil
        log("OFF")
    else
        apply_god_mode()
        log("ON")
    end
end

RegisterConsoleCommandHandler("godmode", function(full_command, parameters, output_device)
    local option = parameters[1]
    if option == nil or option == "status" then
        log(enabled and "status: ON" or "status: OFF")
    elseif option == "on" then
        set_enabled(true)
    elseif option == "off" then
        set_enabled(false)
    else
        log("usage: godmode [on|off|status]")
    end
    return true
end)

RegisterKeyBind(Key.F6, function()
    set_enabled(not enabled)
end)

RegisterKeyBind(Key.F7, function()
    log(enabled and "status: ON" or "status: OFF")
    ExecuteInGameThread(apply_god_mode)
end)

LoopAsync(refresh_ms, function()
    if enabled and not apply_queued then
        apply_queued = true
        ExecuteInGameThread(function()
            apply_god_mode()
            apply_queued = false
        end)
    end
    return false
end)

log("loaded; F6=toggle, F7=status, or use 'godmode on|off|status'")
