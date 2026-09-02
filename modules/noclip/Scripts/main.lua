local UEHelpers = require("UEHelpers")

local noclipEnabled = false

local savedCollisionValue = nil
local savedCapsule = nil
local savedPawn = nil


local MOVE_STEP = 45.0
local VERTICAL_STEP = 35.0

print("[MecchaNoclip] main.lua loaded")


-- ========================================
-- 현재 Pawn 얻기
-- ========================================
local function GetPawn()

    local playerController = UEHelpers.GetPlayerController()

    if not playerController then
        print("[MecchaNoclip] PlayerController not found")
        return nil
    end

    local pawn = playerController.Pawn

    if not pawn then
        print("[MecchaNoclip] Pawn not found")
        return nil
    end

    return pawn
end


-- ========================================
-- Pawn 직접 이동
-- ========================================
local function MovePawn(forwardAmount, rightAmount, upAmount)

    if not noclipEnabled then
        return
    end

    local pawn = GetPawn()

    if not pawn then
        return
    end

    local location = pawn:K2_GetActorLocation()
    local rotation = pawn:K2_GetActorRotation()

    if not location or not rotation then
        print("[MecchaNoclip] Location / Rotation failed")
        return
    end

    local yaw = math.rad(rotation.Yaw)

    -- 전방 방향
    local forwardX = math.cos(yaw)
    local forwardY = math.sin(yaw)

    -- 오른쪽 방향
    local rightX = -math.sin(yaw)
    local rightY = math.cos(yaw)

    local newLocation = {

        X =
            location.X
            + forwardX * forwardAmount
            + rightX * rightAmount,

        Y =
            location.Y
            + forwardY * forwardAmount
            + rightY * rightAmount,

        Z =
            location.Z
            + upAmount
    }

    pawn:K2_SetActorLocation(
        newLocation,
        false,
        {},
        true
    )
end


-- ========================================
-- NOCLIP ON / OFF
-- ========================================
RegisterConsoleCommandHandler(
    "nocliptest",

    function(FullCommand, Parameters, OutputDevice)

        ExecuteInGameThread(function()

            local pawn = GetPawn()

            if not pawn then
                return
            end

            local capsule = pawn.BodyCapsule

            if not capsule then
                print("[MecchaNoclip] BodyCapsule not found")
                return
            end


            -- =========================
            -- NOCLIP ON
            -- =========================
            if not noclipEnabled then

                savedCollisionValue =
                    capsule:GetCollisionEnabled()

                savedCapsule = capsule
                savedPawn = pawn

                print(
                    "[MecchaNoclip] Original Collision = "
                    .. tostring(savedCollisionValue)
                )

                -- Collision OFF
                capsule:SetCollisionEnabled(0)

                print(
                    "[MecchaNoclip] Collision = "
                    .. tostring(
                        capsule:GetCollisionEnabled()
                    )
                )

                -- Gravity OFF
                pawn:SetGravity(
                    true,
                    {
                        X = 0.0,
                        Y = 0.0,
                        Z = 0.0
                    }
                )

                noclipEnabled = true

                print("[MecchaNoclip] NOCLIP ON")
                print("[MecchaNoclip] W/A/S/D = move")
                print("[MecchaNoclip] SPACE = up")
                print("[MecchaNoclip] C = down")


            -- =========================
            -- NOCLIP OFF
            -- =========================
            else

                -- Collision 복구
                if savedCapsule
                    and savedCollisionValue ~= nil then

                    savedCapsule:SetCollisionEnabled(
                        savedCollisionValue
                    )

                    print(
                        "[MecchaNoclip] Collision restored = "
                        .. tostring(
                            savedCapsule:GetCollisionEnabled()
                        )
                    )
                end

                -- Gravity 복구
                if savedPawn then

                    savedPawn:SetGravity(
                        false,
                        {
                            X = 0.0,
                            Y = 0.0,
                            Z = 0.0
                        }
                    )

                end

                noclipEnabled = false

                savedCollisionValue = nil
                savedCapsule = nil
                savedPawn = nil

                print("[MecchaNoclip] NOCLIP OFF")

            end

        end)

        return true
    end
)


-- ========================================
-- W : 앞으로
-- ========================================
RegisterKeyBind(Key.W, function()

    ExecuteInGameThread(function()

        MovePawn(
            MOVE_STEP,
            0.0,
            0.0
        )

    end)

end)


-- ========================================
-- S : 뒤로
-- ========================================
RegisterKeyBind(Key.S, function()

    ExecuteInGameThread(function()

        MovePawn(
            -MOVE_STEP,
            0.0,
            0.0
        )

    end)

end)


-- ========================================
-- A : 왼쪽
-- ========================================
RegisterKeyBind(Key.A, function()

    ExecuteInGameThread(function()

        MovePawn(
            0.0,
            -MOVE_STEP,
            0.0
        )

    end)

end)


-- ========================================
-- D : 오른쪽
-- ========================================
RegisterKeyBind(Key.D, function()

    ExecuteInGameThread(function()

        MovePawn(
            0.0,
            MOVE_STEP,
            0.0
        )

    end)

end)


-- ========================================
-- SPACE : 위
-- ========================================
RegisterKeyBind(Key.SPACE, function()

    ExecuteInGameThread(function()

        MovePawn(
            0.0,
            0.0,
            VERTICAL_STEP
        )

    end)

end)


-- ========================================
-- C : 아래
-- ========================================
RegisterKeyBind(Key.C, function()

    ExecuteInGameThread(function()

        MovePawn(
            0.0,
            0.0,
            -VERTICAL_STEP
        )

    end)

end)