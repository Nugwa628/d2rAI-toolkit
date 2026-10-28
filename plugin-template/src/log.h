// Log helper: one prefix, printf-style formatting, and a "1st / 100th / 10,000th" filter for repeated events.
//
// D2RLoader writes these lines to the plugin's own log (<game>\mods\<Mod>\d2rloader\logs\, context->pluginLogPath).
// A player's log is often all you get after a crash, so log at load: version, D2R version, image stamp, mod, this
// DLL's path, one line per part, the off-switch words, the other plugins. Before a risky action, say what is about to
// happen; a log that ends there tells you what was running.
#pragma once

#include <D2RLPlugin/api.h>

#include <atomic>
#include <cstdint>

namespace plog {

// Call once from D2RLoaderLoadPlugin. `prefix` starts every line ("mod-core: ...").
void Init(const D2RL::PluginContext* context, const char* prefix) noexcept;

void Info(const char* fmt, ...) noexcept;
void Warn(const char* fmt, ...) noexcept;
void Error(const char* fmt, ...) noexcept;

// True for the 1st, 100th, 10,000th and 1,000,000th call on `counter`: log a repeated event without flooding.
//   static std::atomic<uint32_t> s_bad{0};
//   if (plog::Sample(s_bad)) plog::Warn("bad sound id %d (%u so far)", id, s_bad.load());
auto Sample(std::atomic<uint32_t>& counter) noexcept -> bool;

}  // namespace plog
