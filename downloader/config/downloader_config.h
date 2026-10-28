// The one place that makes this downloader yours. Copy this file, change the values, and build with
//   cmake -S downloader -B build/downloader -A x64 -DDL_CONFIG_HEADER=path/to/your_config.h
// Everything the player sees, every file name the downloader writes and the colours of its window come from here.
// Text values are plain string literals (no L"" prefix). Colours are 0xRRGGBB.
#pragma once

// ---- the mod --------------------------------------------------------------------------------------------------------
// Shown to the player: window title, status lines, messages ("<name> needs D2R 3.3", "<name> is installed").
#define DL_MOD_NAME "My Mod"
// The folder under <D2R>\mods\ when a release manifest does not name one (modFolder). One plain word: no spaces needed,
// no slashes. D2RLoader starts the mod with this name (default_mod).
#define DL_MOD_FOLDER "MyMod"
// The window title and the name in dialogs.
#define DL_PRODUCT_TITLE DL_MOD_NAME " Downloader"
// The downloader's file name without ".exe" (also used as the update file name and in the version resource).
#define DL_EXE_NAME "MyMod-Downloader"

// ---- the download site (see docs/release.md and docs/downloader.md) -------------------------------------------------------
// Root of the site that serves channels/, releases/ and objects/. Must end in '/'. The player can override it with --site.
#define DL_SITE_URL "https://files.example.com/mymod/"
#define DL_DEFAULT_CHANNEL "stable"
// Opened by the small "report a problem" button.
#define DL_ISSUES_URL "https://example.com/your-name/your-mod/issues"

// ---- what the downloader writes -------------------------------------------------------------------------------------
// %LOCALAPPDATA%\<this>: the downloader's state, its log and the 7z while it downloads.
#define DL_STATE_FOLDER "MyMod"
// A file at the top of the mod folder that proves the downloader made it. Uninstall and Repair refuse folders without it.
#define DL_MARKER_FILE "mymod-install.json"
// Partial downloads live here, in the mod folder, so a finished file only needs a rename on the same drive.
#define DL_DOWNLOAD_FOLDER "mymod-download"
// File name prefix of YOUR plugin DLL ("mymod-core.dll"). A copy of it outside the mod's own folder loads with every
// mod; the downloader warns about copies and offers to move them away. Leave empty when the mod has no plugin.
#define DL_PLUGIN_DLL_PREFIX "mymod-core"
// Names in a DLL's version resource (ProductName / FileDescription) that also mark a copy of your plugin.
#define DL_PLUGIN_PRODUCT_NAME "MyMod"
// Folders under %LOCALAPPDATA% and %APPDATA% that the downloader must never write into, for example another launcher's
// folder. A classic "Diablo II" folder is always protected. Write each name as a string literal followed by a
// comma. Example: #define DL_PROTECTED_APPDATA_FOLDERS "OtherLauncher", "OtherMod",
#define DL_PROTECTED_APPDATA_FOLDERS

// ---- the game --------------------------------------------------------------------------------------------------------
// The game version the mod needs: major.minor of D2R's version ("3.3" for 3.3.93847). Never the build number.
#define DL_REQUIRED_GAME_VERSION "3.3"
// Shown only: the build the mod was made on.
#define DL_REQUIRED_GAME_BUILD 93847
// Used when a release manifest names no D2RLoader version. D2RLoader is never shipped by the downloader.
#define DL_LOADER_VERSION "1.3.1-beta"
#define DL_LOADER_SITE "https://d2rloader.net/"

// ---- the text in the window ------------------------------------------------------------------------------------------
// Shown under the Install button and at the end of an install.
#define DL_HOW_TO_PLAY \
	"To play, run D2RLoader.exe from the game folder. On first start, open the gear menu on the character screen, " \
	"choose " DL_MOD_FOLDER " as Default Mod, click Apply and restart D2RLoader."
// The footer line (after the downloader's version). Credit the people whose work you port or reuse here.
#define DL_FOOTER "Diablo II: Resurrected is a trademark of Blizzard Entertainment. This mod is not made or endorsed by Blizzard."
// The company / author in the exe's version resource.
#define DL_COMPANY "Your Name"

// ---- look ------------------------------------------------------------------------------------------------------------------
#define DL_COLOR_BACKGROUND 0x16191D  // window
#define DL_COLOR_PANEL 0x22272E       // the status panel
#define DL_COLOR_ACCENT 0x5B9BD5      // titles, buttons, progress bar
#define DL_COLOR_ACCENT_BRIGHT 0x9CCBF2  // a hovered button
#define DL_COLOR_TEXT 0xD8DEE6        // status lines
#define DL_COLOR_DIM 0x7D8794         // footer and disabled buttons
#define DL_COLOR_ERROR 0xE06C75       // warnings and failed steps
// System font names (nothing is embedded). The title font draws the buttons and, without a logo, the mod name.
#define DL_FONT_TITLE L"Georgia"
#define DL_FONT_BODY L"Segoe UI"
// Optional picture files are chosen at build time: -DDL_LOGO_FILE=<png or gif> (animated GIFs play), -DDL_ICON_FILE=<ico>,
// -DDL_BACKGROUND_FILE=<900x600 png>. Without them the window is plain colour and the mod name is drawn as text.
