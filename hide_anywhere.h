#pragma once
#include "feature.h"

// Hide anywhere — two effects bundled under one toggle:
//   1. cLeon Survivor buried-check bypass: zero FilledValue every frame so
//      ReceiveTick never crosses the 5.0 threshold and the reveal stencil
//      multicast never fires.
//   2. LINK / Main interaction reach: expand NearInteract radius/angle and
//      open the character + camera-side gates so distant props become
//      selectable.
namespace features::hide_anywhere {
    void tick(const frame_context& ctx);
}
