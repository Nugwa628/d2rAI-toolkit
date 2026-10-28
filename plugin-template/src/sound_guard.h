// Sound guard (sound_guard.cpp): a sound id outside sounds.txt is skipped instead of crashing the client, and the
// loaded sounds table is checked after every table load.
//
// Why (knowledge/engine-limits.md "Sounds", knowledge/d2rloader.md):
//  - a sound name that does not resolve becomes id -1, and D2R's client sound readers check the id against the row
//    count but then read through a null record: the client crashes when a monster with that sound comes into view;
//  - on the start where D2RLoader builds its table cache (first start after installing or updating D2RLoader or D2R)
//    the game loads D2R's own sounds table, not the mod's: every new sound is -1 until the next start.
#pragma once

#include <D2RLPlugin/api.h>

#include <cstddef>

// `modSoundRows`: rows of the mod's sounds.txt (0 = the mod adds no sounds: no restart warning). Engine part: call
// only on the expected image. Returns true when every hook went in.
auto InstallSoundGuard(const D2RL::PluginContext* context, int modSoundRows, char* report, size_t reportSize) noexcept -> bool;

// "sounds.txt N rows (mod M), K bad sound ids skipped (last id I in 0x... from +0x...)".
void SoundGuardStatus(char* out, size_t size) noexcept;
