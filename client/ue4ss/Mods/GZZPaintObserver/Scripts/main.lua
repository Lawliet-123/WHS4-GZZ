-- Read-only gameplay observer. Never invoke painting/RPCs or change their arguments.
local source = debug.getinfo(1, "S").source:gsub("^@", "")
local scripts = source:match("^(.*[/\\])")
assert(scripts, "GZZPaintObserver: cannot resolve Scripts directory")
local observer = dofile(scripts .. "observer.lua").new(scripts .. "../session.control")
local function tick()
    local ok, err = pcall(function() observer:tick() end)
    if not ok then print("[GZZPaintObserver] tick failed: " .. tostring(err) .. "\n") end
end
if type(LoopInGameThreadWithDelay) == "function" then
    LoopInGameThreadWithDelay(100, function() tick(); return false end)
else
    local queued = false
    LoopAsync(100, function()
        if not queued then
            queued = true
            local ok = pcall(function()
                ExecuteInGameThread(function() tick(); queued = false end)
            end)
            if not ok then queued = false end
        end
        return false
    end)
end
ModRef.OnUnload = function() observer:close(); observer:unhook() end
print("[GZZPaintObserver] loaded; waiting for Python session control\n")
