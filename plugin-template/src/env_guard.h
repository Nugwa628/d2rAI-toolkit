// Environment guard: is the plugin that runs the one you shipped, for the mod you shipped it with?
//
// What goes wrong on players' machines (knowledge/d2rloader.md):
//  - D2RLoader's default_mod (<game>\d2rloader\config\d2rloader.toml) names another or an old mod folder, so the game
//    runs that folder and not what the installer just wrote;
//  - an old copy of this plugin sits in the game-wide <game>\d2rloader\plugins\ (it loads with every mod) or in another
//    mod's plugins folder, so two copies run, or the old one;
//  - other gameplay plugins hook the same code. Don't make the plugin compatible with them: detect them, name them in
//    the log and the status command, and tell players to remove them.
//
// D2RLoader loads the active mod's plugins first and the game-wide ones after, so the check runs at load and again
// about 15 s later (a one-shot thread). No game address is used: this part runs even on a wrong image.
#pragma once

#include <D2RLPlugin/api.h>

#include <cstddef>

// `expectedMod` is the mod folder this plugin ships in (empty: skip the mod checks). Logs a warning for each problem.
void StartEnvironmentGuard(const D2RL::PluginContext* context, const char* expectedMod) noexcept;

// "ok" or the warnings, for the status command.
void EnvironmentStatus(char* out, size_t size) noexcept;

// "other plugins: none" or "other plugins: <file> (mod <name> | game-wide), ...".
void OtherPlugins(char* out, size_t size) noexcept;
