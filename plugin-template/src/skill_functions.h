// New server skill "do" functions in free slots of D2R's srvdofunc table (build 93847). See skill_functions.cpp.
#pragma once

#include <D2RLPlugin/api.h>

#include <cstddef>

// Writes every function of the table into its slot. Returns how many were installed; report gets one entry per slot.
auto InstallSkillFunctions(const D2RL::PluginContext* context, char* report, size_t reportSize) noexcept -> int;
auto SkillFunctionCount() noexcept -> int;
