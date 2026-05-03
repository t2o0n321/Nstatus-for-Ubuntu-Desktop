-- nstatus_hooks.lua
-- Left-clicking anywhere in the top CLICK_ZONE_Y pixels (title + mode button)
-- toggles Simple/Full mode and immediately regenerates conky_data.txt so the
-- display updates within the next Conky redraw cycle (~2 s) rather than waiting
-- for the daemon's next flush.

local HOME        = os.getenv("HOME")
local FLAG        = HOME .. "/.local/share/nstatus/simple_mode"
local REGEN       = HOME .. "/.config/nstatus/scripts/regen_conky.sh"
local CLICK_ZONE_Y = 80   -- px from top; covers 4-line header at any font scaling

function conky_mouse_hook(button, modifiers, x, y, event_type)
    if event_type ~= "button_press" or button ~= 1 or y > CLICK_ZONE_Y then
        return
    end

    -- Toggle flag file
    local f = io.open(FLAG, "r")
    if f then
        f:close()
        os.remove(FLAG)
    else
        local g = io.open(FLAG, "w")
        if g then g:close() end
    end

    -- Regenerate conky_data.txt immediately (background, non-blocking)
    os.execute("bash " .. REGEN .. " &")
end
