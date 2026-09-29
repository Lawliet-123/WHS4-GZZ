-- UE4SS UFunction execution observer, NOT a packet capture or an enforcement mod.
local M = {}
local function safe(fn) local ok, v = pcall(fn); if ok then return v end end
local function valid(o) return o ~= nil and safe(function() return o:IsValid() end) == true end
local function unwrap(v) return safe(function() return v:get() end) or v end
local function prop(o, name) return safe(function() return o[name] end) end
local function name(o) if valid(o) then return safe(function() return o:GetFullName() end) end end
local function number(v) if type(v) == "number" and v == v and math.abs(v) < math.huge then return v end end
local function boolean(v) if type(v) == "boolean" then return v end end
local function encode(v)
    local t = type(v)
    if t == "nil" then return "null" end
    if t == "boolean" then return tostring(v) end
    if t == "number" then return number(v) and tostring(v) or "null" end
    if t == "string" then
        return '"' .. v:gsub('[%z\1-\31\\"]', function(c)
            if c == '"' then return '\\"' end
            if c == '\\' then return '\\\\' end
            return string.format("\\u%04x", c:byte())
        end) .. '"'
    end
    if t ~= "table" then return "null" end
    local parts = {}
    if #v > 0 then
        for _, x in ipairs(v) do parts[#parts + 1] = encode(x) end
        return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, x in pairs(v) do parts[#parts + 1] = encode(tostring(k)) .. ":" .. encode(x) end
    table.sort(parts)
    return "{" .. table.concat(parts, ",") .. "}"
end
M.encode = encode

local component = "/Script/PenguinHotel.RuntimePaintableComponent:"
local relay = "/Script/PenguinHotel.RuntimePaintRelayComponent:"
local function definitions()
    local result = {}
    local function add(prefix, id, category, param)
        result[#result + 1] = {id=id, prefix=prefix, category=category, param=param,
            calls=0, errors=0, registered=false, attempts=0}
    end
    add(nil, "InpActEvt_IA_PaintStart_K2Node_EnhancedInputActionEvent_17", "input")
    add(nil, "InpActEvt_IA_PaintShot_K2Node_EnhancedInputActionEvent_13", "input")
    add(nil, "PaintTick", "paint_tick")
    for _, id in ipairs({"BeginStroke", "EndStroke", "FlushRecordedStrokesToServer", "SendStrokeBatchToServer"}) do
        add(component, id, "stroke")
    end
    add(component, "PaintAtUVWithBrush", "apply", "uv_brush")
    add(component, "PaintAtUV", "apply", "uv")
    add(component, "PaintAtScreenPosition", "apply")
    for _, id in ipairs({"ServerPaintBatch", "ServerSendStrokeBatch", "RequestStrokeBatchOnServer", "SendCustomStrokeBatchToServer",
        "ServerCompactPaintBatch", "MulticastPaintBatch", "MulticastPaintBatchToOthers", "MulticastCompactPaintBatch", "MulticastCompactPaintBatchToOthers"}) do
        add(component, id, id:find("Multicast") and "multicast" or "rpc_or_request", "batch")
    end
    for _, id in ipairs({"ServerPaint", "ServerSendPaint", "ServerCompactPaint", "MulticastPaint", "MulticastPaintToOthers"}) do
        add(component, id, "rpc_or_multicast", "stroke")
    end
    for _, id in ipairs({"ServerPackedPaintBatch", "MulticastPackedPaintBatch", "MulticastPackedPaintBatchToOthers"}) do
        add(component, id, "packed_rpc", "packed")
    end
    add(relay, "ServerRelayStrokeBatch", "relay", "relay_batch")
    add(relay, "ServerRelayCompactStrokeBatch", "relay", "relay_batch")
    add(relay, "ServerRelayPackedStrokeBatch", "relay", "relay_packed")
    return result
end

local function uv(v) return {x=number(prop(v, "X")), y=number(prop(v, "Y"))} end
local function brush(v)
    return {radius=number(prop(v,"Radius")), hardness=number(prop(v,"Hardness")),
        opacity=number(prop(v,"Opacity")), spacing=number(prop(v,"Spacing"))}
end
local function stroke(v)
    local guid = prop(v,"ReplicationSourceId")
    return {uv=uv(prop(v,"Uv")), brush=brush(prop(v,"BrushSettings")),
        replication_source_id={a=number(prop(guid,"A")), b=number(prop(guid,"B")), c=number(prop(guid,"C")), d=number(prop(guid,"D"))},
        target_channel=number(prop(v,"TargetChannel"))}
end
local function batch(v)
    local strokes = prop(v, "Strokes")
    local count = number(safe(function() return strokes:GetArrayNum() end))
    -- No whole-array walk/copy. First stroke only; retain exact count when readable.
    local first = count and count > 0 and safe(function() return unwrap(strokes[1]) end) or nil
    return {stroke_count=count, first_stroke=first and stroke(first) or nil,
        parameter_read_ok=count ~= nil, sample_limit=1}
end

function M.new(control_path)
    local self = {control_path=control_path, hooks=definitions(), queue={}, sequence=0,
        dropped=0, write_errors=0, callback_errors=0, ticks=0, guard=false, max_queue=8192,
        active=nil, file=nil, local_pawn=nil, local_pawn_name=nil, world_name=nil}

    function self:clock()
        local millis = safe(function()
            local lib = StaticFindObject("/Script/Engine.Default__KismetMathLibrary")
            return math.floor(lib:ToUnixTimestampDouble(lib:UtcNow()) * 1000)
        end)
        if number(millis) then return millis, 1 end
        return os.time() * 1000, 1000  -- Visible coarse fallback, never pretend millisecond precision.
    end

    function self:emit(value)
        if not self.active then return end
        self.sequence = self.sequence + 1
        if #self.queue >= self.max_queue then self.dropped = self.dropped + 1; return end
        local now, resolution = self:clock()
        local elapsed = now - tonumber(self.active.start_unix_ms)
        value.timestamp_ms = math.max(0, elapsed)
        if elapsed < 0 then value.unclamped_elapsed_ms=elapsed; value.clock_timestamp_clamped=true end
        value.clock_resolution_ms = resolution
        value.clock_source = resolution == 1 and "KismetMathLibrary.UTC" or "os.time.coarse"
        value.session_id, value.player_id = self.active.session_id, self.active.player_id
        value.session_token, value.sequence = self.active.token, self.sequence
        self.queue[#self.queue + 1] = encode(value) .. "\n"
    end

    function self:flush()
        if not self.file or #self.queue == 0 then return end
        local ok = safe(function()
            assert(self.file:write(table.concat(self.queue)))
            assert(self.file:flush())
            return true
        end)
        if not ok then
            self.write_errors = self.write_errors + 1
            self.dropped = self.dropped + #self.queue
            print("[GZZPaintObserver] log write failed; data incomplete\n")
        end
        self.queue = {}
    end

    function self:close()
        if self.active then self:emit({kind="collector_stop", dropped=self.dropped, write_errors=self.write_errors}) end
        self:flush()
        if self.file then self.file:close() end
        self.file, self.active = nil, nil
    end

    function self:control()
        local f = io.open(self.control_path, "r")
        if not f then self:close(); return end
        local text = f:read(16385); f:close()
        if not text or #text > 16384 then self:close(); return end
        local cfg = {}
        for line in text:gmatch("[^\r\n]+") do
            local k,v = line:match("^([a-z_]+)=(.*)$"); if k then cfg[k]=v end
        end
        local age = os.time() * 1000 - (tonumber(cfg.heartbeat_unix_ms) or 0)
        if cfg.protocol ~= "1" or cfg.active ~= "1" or age > 10000 or age < -10000
            or not cfg.token or not cfg.token:match("^%x+$") or #cfg.token ~= 32
            or not tonumber(cfg.start_unix_ms) or not cfg.output
            or not cfg.output:match("paint_calls%.jsonl$") then self:close(); return end
        if self.active and self.active.token == cfg.token then return end
        self:close()
        local output, err = io.open(cfg.output, "a")
        if not output then print("[GZZPaintObserver] cannot open log: " .. tostring(err) .. "\n"); return end
        self.file, self.active = output, cfg
        self.sequence, self.dropped, self.write_errors, self.callback_errors = 0,0,0,0
        for _,h in ipairs(self.hooks) do h.calls, h.errors = 0,0 end
        local version = safe(function() local a,b,c=UE4SS.GetVersion(); return string.format("%s.%s.%s",a,b,c) end)
        self:emit({kind="collector_start", collector_version="0.2.0", ue4ss_version=version,
            behavioral_scoring=false, scope="UFunction_execution_not_packets"})
        print("[GZZPaintObserver] session=" .. tostring(cfg.session_id) .. "\n")
    end

    function self:context(object)
        if not valid(object) then return {object_valid=false, painter_identity="unresolved"} end
        if not valid(self.local_pawn) then self.local_pawn,self.local_pawn_name=nil,nil end
        local owner = safe(function() return object:GetOwner() end)
        if not valid(owner) then owner = object end
        local object_name, owner_name = name(object), name(owner)
        local context_local, owner_local
        if self.local_pawn_name then
            context_local,owner_local = object_name == self.local_pawn_name, owner_name == self.local_pawn_name
        end
        return {object=object_name, owner=owner_name, world=self.world_name, object_valid=true,
            local_pawn=self.local_pawn_name,
            context_is_local_pawn=context_local, owner_is_local_pawn=owner_local,
            owner_has_authority=boolean(safe(function() return owner:HasAuthority() end)),
            owner_locally_controlled=boolean(safe(function() return owner:IsLocallyControlled() end)),
            owner_local_role=number(safe(function() return owner:GetLocalRole() end)),
            -- Owner of a painted object is NOT necessarily the painter/sender.
            painter_identity="unresolved",
            local_is_paint_mode=boolean(prop(self.local_pawn, "IsPaintMode")),
            local_is_brushing=boolean(prop(self.local_pawn, "IsBrushing"))}
    end

    function self:callback(h, context, ...)
        if not self.active or self.guard then return end
        self.guard = true
        local args = {...}
        h.calls = h.calls + 1
        if #self.queue >= self.max_queue then
            self.dropped, self.sequence = self.dropped + 1, self.sequence + 1
            self.guard = false
            return
        end
        local ok, err = pcall(function()
            local value = {kind="call", function_name=h.id, function_path=h.path,
                category=h.category, hook_phase=h.prefix and "pre" or "post",
                context=self:context(unwrap(context))}
            local first = unwrap(args[1])
            if h.param == "batch" then value.parameters=batch(first)
            elseif h.param == "relay_batch" then
                value.paint_component=name(first); value.parameters=batch(unwrap(args[2]))
            elseif h.param == "packed" then value.parameters={stroke_count=number(unwrap(args[2]))}
            elseif h.param == "relay_packed" then
                value.paint_component=name(first); value.parameters={stroke_count=number(unwrap(args[3]))}
            elseif h.param == "stroke" then value.parameters=stroke(first)
            elseif h.param == "uv" or h.param == "uv_brush" then
                value.parameters={uv=uv(first)}
                if h.param == "uv_brush" then value.parameters.brush=brush(unwrap(args[3])) end
            end
            self:emit(value)
        end)
        if not ok then
            h.errors, self.callback_errors = h.errors + 1, self.callback_errors + 1
            h.last_callback_error = tostring(err):sub(1,500)
        end
        self.guard = false
        -- No return value: do not override the game's original return/parameters.
    end

    function self:refresh()
        self.local_pawn = nil
        local helpers = safe(function() return require("UEHelpers") end)
        local controller = helpers and safe(function() return helpers:GetPlayerController() end)
        local pawn = prop(controller, "Pawn")
        if valid(pawn) then self.local_pawn = pawn end
        self.local_pawn_name = name(self.local_pawn)
        local world = safe(function() return controller:GetWorld() end)
        self.world_name = name(world)
        local driver = prop(world, "NetDriver")
        local is_server = boolean(safe(function()
            if not valid(world) then return nil end
            return StaticFindObject("/Script/Engine.Default__KismetSystemLibrary"):IsServer(world)
        end))
        self.network = {world=self.world_name, net_driver=name(driver), is_server=is_server,
            mode=valid(driver) and (is_server == true and "host" or is_server == false and "client" or "unknown") or "standalone_or_unknown"}
        local character = safe(function() return FindFirstOf("BP_FirstPersonCharacter_cLeon_Character_C") end)
        local class_name = valid(character) and name(safe(function() return character:GetClass() end)) or nil
        local bp_path = class_name and class_name:match("^%S+%s+(.+)$")
        for _,h in ipairs(self.hooks) do
            local path = (h.prefix or (bp_path and bp_path .. ":"))
            path = path and path .. h.id
            local fn = path and safe(function() return StaticFindObject(path) end)
            local address = valid(fn) and safe(function() return tostring(fn:GetAddress()) end) or nil
            if not address then
                h.available = false
            elseif not h.registered or h.address ~= address or h.path ~= path then
                if h.registered then safe(function() UnregisterHook(h.path,h.pre_id,h.post_id) end) end
                h.path, h.attempts, h.available = path, h.attempts + 1, true
                local ok,a,b = pcall(function()
                    return RegisterHook(path, function(ctx, ...) self:callback(h,ctx,...) end)
                end)
                h.registered = ok and type(a) == "number" and type(b) == "number"
                if h.registered then
                    h.address,h.pre_id,h.post_id = address,a,b
                    h.last_error = nil
                else h.last_error = tostring(a):sub(1,500) end
            else h.available=true end
        end
    end

    function self:health()
        local hooks, count = {},0
        for _,h in ipairs(self.hooks) do
            if h.registered and h.available then count = count + 1 end
            hooks[#hooks + 1] = {function_name=h.id, path=h.path, registered=h.registered,
                available=h.available or false, calls=h.calls, attempts=h.attempts, errors=h.errors,
                last_error=h.last_error, last_callback_error=h.last_callback_error}
        end
        self:emit({kind="health", hooks=hooks, registered_hooks=count, expected_hooks=#self.hooks,
            network=self.network, local_pawn=self.local_pawn_name,
            dropped=self.dropped, write_errors=self.write_errors, callback_errors=self.callback_errors})
    end

    function self:tick()
        self:control()
        self.ticks = self.ticks + 1
        if self.active and (self.ticks % 10 == 1) then self:refresh(); self:health() end
        self:flush()
    end
    function self:unhook()
        for _,h in ipairs(self.hooks) do
            if h.registered then safe(function() UnregisterHook(h.path,h.pre_id,h.post_id) end) end
        end
    end
    return self
end
return M
