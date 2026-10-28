// Finding D2R and checking its build: the parsers (testable without Windows). The registry / file system side is in
// src/win/game.cpp.
#pragma once

#include "config.h"

#include <string>
#include <vector>

namespace dl {

// The game version the mod needs (DL_REQUIRED_GAME_VERSION): major.minor of D2R's version ("3.3" of "3.3.93847"). Never the build number: builds of
// one game version differ by platform (Steam and Battle.net) and hotfix, and D2RLoader supports the
// game version, not a build. A plugin can still check the game image at run time and stay off
// on one it was not made for.
constexpr const char* RequiredGameVersion = DL_REQUIRED_GAME_VERSION;
constexpr int RequiredBuild = DL_REQUIRED_GAME_BUILD;  // the build the mod was made on; shown only

// "major.minor" of a version string: "3.3.93847" -> "3.3", "3.3.0" -> "3.3", "1.0.0.93847" -> "1.0"; "" when none.
std::string GameVersion(const std::string& version);

// .build.info of a Blizzard (CASC) install: '|' separated, first line "Name!TYPE:n|...". Returns the "Version" of
// the active row ("3.3.93847"), or "" when there is none.
std::string BuildInfoVersion(const std::string& text);

// Last number of a version string: "3.3.93847" -> 93847, "1.0.0.93847" -> 93847; 0 when there is none.
int BuildNumber(const std::string& version);

// Every "path" value of Steam's libraryfolders.vdf (KeyValues text), unescaped ("C:\\Steam" -> "C:\Steam").
std::vector<std::string> SteamLibraryPaths(const std::string& vdf);

// Value of a top-level key of an appmanifest_*.acf ("installdir", "name", "appid"), "" when missing.
std::string AcfValue(const std::string& acf, const std::string& key);

// True when an appmanifest names D2R (its name or install folder).
bool AcfIsD2R(const std::string& acf);

enum class BuildVerdict { Ok, Older, Newer, Unknown };
struct BuildCheck {
	BuildVerdict verdict = BuildVerdict::Unknown;
	int build = 0;
	std::string version;  // the game version found ("3.3"), "" when unknown
	std::string message;  // one line for the status bar
};
// `infoVersion` from .build.info, `exeVersion` the file version of D2R.exe ("" when unreadable). The game version
// (major.minor) of .build.info (else of D2R.exe) must equal `required` ("3.3"); the build number is only shown.
BuildCheck CheckBuild(const std::string& infoVersion, const std::string& exeVersion,
                      const std::string& required = RequiredGameVersion);

}  // namespace dl
