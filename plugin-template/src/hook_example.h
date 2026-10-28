// Example inline hook on a D2R function (build 93847). See hook_example.cpp.
#pragma once

#include <D2RLPlugin/api.h>

#include <cstddef>
#include <cstdint>

auto InstallHookExample(const D2RL::PluginContext* context, char* report, size_t reportSize) noexcept -> bool;
auto HookExampleCalls() noexcept -> int64_t;  // how often the hooked function ran since load
