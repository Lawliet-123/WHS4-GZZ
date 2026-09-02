#include "hide_anywhere.h"
#include "../offsets/offsets.h"

namespace features::hide_anywhere {

void tick(const frame_context& ctx) {
    if (!vars::hide_anywhere || !ctx.local_is_hider) return;
    uint8_t* me = (uint8_t*)ctx.my_player;
    const std::string& cls = ctx.local_class_name;

    // Verified from a Dumper-7 capture taken while the Survivor class was loaded.
    // Keep the class gate: these fields do not belong to non-Survivor pawns.
    if (cls.find("Survivor") != std::string::npos) {
        *(double*)(me + offsets::cleon_survivor::FilledValue) = 0.0;
        *(int32_t*)(me + offsets::cleon_survivor::PreStencil) = 0;
    }

    // Interaction reach + camera-side gates. Main_C-inherited offsets — safe
    // to write unconditionally for any hider variant.
    using namespace offsets::human_character;
    *(double*)(me + InteractLength)    = 5000.0;
    *(bool*)(me + IsTalkNow)           = false;
    *(bool*)(me + EnableInteract)      = true;
    *(double*)(me + IsInViewCheckLate) = 1e9;
    *(bool*)(me + UseNearInteract)     = true;

    if (void* near_int = *(void**)(me + BPC_NearInteract)) {
        using namespace offsets::near_interact;
        *(double*)((uint8_t*)near_int + SearchRadius)   = 5000.0;
        *(double*)((uint8_t*)near_int + Angle)          = 360.0;
        *(double*)((uint8_t*)near_int + AngleBias)      = 360.0;
        *(bool*)((uint8_t*)near_int + IgnoreUpVector)   = true;
    }
    if (AActor* held = *(AActor**)(me + HaveActor_R)) {
        if (((UObject*)held)->GetName().find("Camera") != std::string::npos) {
            uint8_t* cam = (uint8_t*)held;
            using namespace offsets::camera_base;
            *(double*)(cam + EnableDistance)        = 5000.0;
            *(double*)(cam + EnableDistanceGimmick) = 5000.0;
            *(double*)(cam + Is_in_View_Check_Late) = 1e9;
        }
    }
}

} // namespace features::hide_anywhere
