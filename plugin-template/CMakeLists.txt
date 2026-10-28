# D2RLoader plugin template (D2R 3.3.0 build 93847, D2RLoader 1.3.1, PluginSDK 0.3.0).
#
# Build:  powershell -ExecutionPolicy Bypass -File build.ps1            (or by hand:)
#         cmake -S . -B build -A x64 -DMODKIT_PLUGIN_NAME=mod-core
#         cmake --build build --config Release
# Output: build/Release/<MODKIT_PLUGIN_NAME>.dll -> copy to <game>/mods/<Mod>/d2rloader/plugins/
#
# MODKIT_SDK_DIR points at the PluginSDK checkout. The default fits this repo (plugin-template/../third_party);
# after copying the template into your own mod project, pass -DMODKIT_SDK_DIR=<path to PluginSDK>.
cmake_minimum_required(VERSION 3.29)
project(modkit_plugin LANGUAGES CXX)

set(MODKIT_PLUGIN_NAME "mod-core" CACHE STRING "Plugin id, DLL name, console command and <name>-off.txt prefix")
set(MODKIT_PLUGIN_VERSION "0.1.0" CACHE STRING "PluginInfo::version (shared plugins must match in multiplayer)")
set(MODKIT_MOD_NAME "" CACHE STRING "Mod folder the plugin ships in (mods/<name>); the environment guard warns when another runs")
set(MODKIT_SOUND_ROWS "0" CACHE STRING "Rows of the mod's sounds.txt (0 = no new sounds); below it the sound guard asks for a restart")
set(MODKIT_SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/PluginSDK" CACHE PATH "D2RLoader PluginSDK directory")

set(D2RLPLUGIN_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
add_subdirectory(${MODKIT_SDK_DIR} PluginSDK EXCLUDE_FROM_ALL)

add_library(modkit_plugin SHARED
	src/plugin.cpp
	src/d2r.cpp
	src/log.cpp
	src/off_switch.cpp
	src/env_guard.cpp
	src/sound_guard.cpp
	src/item_type_check.cpp
	src/skill_functions.cpp
	src/hook_example.cpp
)
target_link_libraries(modkit_plugin PRIVATE D2RLPlugin::D2RLPlugin)
target_compile_definitions(modkit_plugin PRIVATE
	MODKIT_PLUGIN_NAME="${MODKIT_PLUGIN_NAME}"
	MODKIT_PLUGIN_VERSION="${MODKIT_PLUGIN_VERSION}"
	MODKIT_MOD_NAME="${MODKIT_MOD_NAME}"
	MODKIT_SOUND_ROWS=${MODKIT_SOUND_ROWS}
)
set_target_properties(modkit_plugin PROPERTIES OUTPUT_NAME "${MODKIT_PLUGIN_NAME}")
if(MSVC)
	target_compile_options(modkit_plugin PRIVATE /W4 /permissive- /EHsc /utf-8)
	set_property(TARGET modkit_plugin PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")  # static CRT: no VC++ runtime dependency
endif()
