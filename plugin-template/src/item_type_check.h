// Item type id self-check (item_type_check.cpp): a startup check, no patch.
//
// D2R.exe asks "is the item of type N" (0x373890(item, N), build 93847) with N written into the code. If the mod's
// itemtypes.txt gives those types other ids (a classic mod lacks D2R 3.x's `warl` row at 67, so a naive merge shifts
// every later id by one), D2R's rune test hits circlets: picking one up asserts at ItemMode.cpp:676 and the game hangs
// or dies, and socketed runes apply nothing. Fix the data (keep D2R's rows at D2R's numbers); this part reports
// whether it is fixed. knowledge/porting-classic-mods.md section 4.
#pragma once

#include <D2RLPlugin/api.h>

#include <cstddef>

// Checks that each call site still loads D2R's id (proves the image) and that the mod's id for that code
// (item_type_ids.h) is the same. Logs each mismatch. True when everything matches.
auto CheckItemTypeIds(const D2RL::PluginContext* context, char* report, size_t reportSize) noexcept -> bool;
