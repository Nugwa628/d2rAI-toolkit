// The downloader's build-time settings: downloader_config.h (config/ in this repo, or your own copy chosen with
// -DDL_CONFIG_HEADER=...), plus two helpers to use its text in wide string literals.
#pragma once

#ifdef DL_CONFIG_HEADER_FILE
#include DL_CONFIG_HEADER_FILE
#else
#include "downloader_config.h"
#endif

// DL_W(DL_MOD_NAME) is L"My Mod": the macro argument is expanded first, then the L prefix is glued on.
#define DL_W_(x) L##x
#define DL_W(x) DL_W_(x)
