# Mod downloader (docs/downloader.md). One static exe, no runtime to install: WinHTTP, Win32 + GDI+, the LZMA SDK's
# 7z decoder (third_party/lzma). Everything that is yours (mod name, site URL, colours, file names) is in one header,
# config/downloader_config.h; copy it and point -DDL_CONFIG_HEADER at your copy.
#   cmake -S downloader -B build/downloader -A x64 [-DDL_CONFIG_HEADER=my_config.h] [-DDL_LOGO_FILE=logo.png]
#   cmake --build build/downloader --config Release
cmake_minimum_required(VERSION 3.20)
project(mod_downloader LANGUAGES C CXX RC)

set(DL_CONFIG_HEADER "${CMAKE_CURRENT_SOURCE_DIR}/config/downloader_config.h" CACHE FILEPATH "your copy of downloader_config.h")
set(DL_LOGO_FILE "" CACHE FILEPATH "optional logo, PNG or animated GIF (drawn in the window's top part, about 388 x 134)")
set(DL_BACKGROUND_FILE "" CACHE FILEPATH "optional background, PNG, 900 x 600")
set(DL_ICON_FILE "${CMAKE_CURRENT_SOURCE_DIR}/res/placeholder.ico" CACHE FILEPATH "the exe's icon")
set(DL_VERSION "" CACHE STRING "downloader version x.y.z (default: the VERSION file). The self-update compares it.")

include(third_party/lzma/lzma.cmake)

# ---- read the few values the resource script needs from the config header
get_filename_component(DL_CONFIG_HEADER "${DL_CONFIG_HEADER}" ABSOLUTE)
if(NOT EXISTS "${DL_CONFIG_HEADER}")
	message(FATAL_ERROR "config header ${DL_CONFIG_HEADER} not found")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${DL_CONFIG_HEADER}")
function(dl_config_string name out)
	file(STRINGS "${DL_CONFIG_HEADER}" line REGEX "^#define ${name} \"")
	if(NOT line)
		message(FATAL_ERROR "${DL_CONFIG_HEADER} has no '#define ${name} \"...\"'")
	endif()
	list(GET line 0 first)
	string(REGEX REPLACE "^#define ${name} \"([^\"]*)\".*" "\\1" value "${first}")
	set(${out} "${value}" PARENT_SCOPE)
endfunction()
dl_config_string(DL_MOD_NAME DL_MOD_NAME)
dl_config_string(DL_EXE_NAME DL_EXE_NAME)
dl_config_string(DL_COMPANY DL_COMPANY)

if(NOT DL_VERSION)
	file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" DL_VERSION LIMIT_COUNT 1)
endif()
if(NOT DL_VERSION MATCHES "^([0-9]+)[.]([0-9]+)[.]([0-9]+)$")
	message(FATAL_ERROR "downloader version '${DL_VERSION}' is not x.y.z")
endif()
set(DL_VERSION_NUM "${CMAKE_MATCH_1},${CMAKE_MATCH_2},${CMAKE_MATCH_3},0")
message(STATUS "${DL_EXE_NAME} ${DL_VERSION} for ${DL_MOD_NAME}")

# ---- resource script: manifest, icon, optional pictures (resource ids 101 background, 102 logo; src/win/window.cpp)
set(DL_MANIFEST_FILE "${CMAKE_CURRENT_SOURCE_DIR}/res/downloader.manifest")
set(DL_ICON_FILE_RC "${DL_ICON_FILE}")
set(DL_RC_PICTURES "")
if(DL_BACKGROUND_FILE)
	string(APPEND DL_RC_PICTURES "101 10 \"${DL_BACKGROUND_FILE}\"\n")
endif()
if(DL_LOGO_FILE)
	string(APPEND DL_RC_PICTURES "102 10 \"${DL_LOGO_FILE}\"\n")
endif()
configure_file(res/downloader.rc.in "${CMAKE_CURRENT_BINARY_DIR}/downloader.rc" @ONLY)

set(core src/core/sha256.cpp src/core/json.cpp src/core/manifest.cpp src/core/detect.cpp src/core/paths.cpp src/core/sevenzip.cpp)
add_executable(mod_downloader WIN32 src/win/main.cpp src/win/window.cpp src/win/app.cpp src/win/game.cpp src/win/http.cpp src/win/util.cpp
               ${core} ${DL_LZMA_SOURCES} "${CMAKE_CURRENT_BINARY_DIR}/downloader.rc")
set_target_properties(mod_downloader PROPERTIES OUTPUT_NAME "${DL_EXE_NAME}")
target_compile_features(mod_downloader PRIVATE cxx_std_20)
target_include_directories(mod_downloader PRIVATE ${DL_LZMA_INCLUDE})
target_compile_definitions(mod_downloader PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX _WIN32_WINNT=0x0A00
                           DL_CONFIG_HEADER_FILE="${DL_CONFIG_HEADER}" DL_VERSION="${DL_VERSION}")
# Signing is a separate, later step (docs/downloader.md "Release and signing"): the build leaves the unsigned exe and
# its sha256 side by side.
add_custom_command(TARGET mod_downloader POST_BUILD
                   COMMAND ${CMAKE_COMMAND} -DEXE=$<TARGET_FILE:mod_downloader> -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/sha256.cmake
                   VERBATIM)
target_link_libraries(mod_downloader PRIVATE winhttp comctl32 shell32 ole32 version advapi32 user32 gdi32 wintrust crypt32
                      gdiplus shlwapi dwmapi)
if(MSVC)
	target_compile_options(mod_downloader PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4 /permissive- /EHsc> /utf-8 /O1)
	target_link_options(mod_downloader PRIVATE /MANIFEST:NO)  # the manifest comes from the resource script
	set_property(TARGET mod_downloader PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")  # no VC++ runtime to install
endif()

add_subdirectory(tests)
