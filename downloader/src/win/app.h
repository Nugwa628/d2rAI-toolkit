// What the downloader does (docs/downloader.md): find D2R, put the mod release into <D2R>\mods\the mod (from the
// release's 7z or from single-file objects), check D2RLoader, Repair and Uninstall, update itself. Used by the window
// (window.cpp) and the CLI (main.cpp). It never starts the game: the player starts D2RLoader.exe.
#pragma once

#include "../core/json.h"
#include "../core/config.h"
#include "../core/manifest.h"
#include "../core/paths.h"
#include "game.h"

#include <atomic>
#include <functional>
#include <string>
#include <vector>

namespace dl {

#ifndef DL_VERSION
#define DL_VERSION "1.0.0"  // the downloader's version; CMake reads it from downloader/VERSION and passes it in
#endif
constexpr const char* LauncherVersion = DL_VERSION;
constexpr const char* ProductTitle = DL_PRODUCT_TITLE;
// The download site (docs/release.md "Hosting layout"): channels/<channel>.json, releases/<v>/, objects/
constexpr const char* DefaultSite = DL_SITE_URL;
constexpr const char* DefaultChannel = DL_DEFAULT_CHANNEL;
constexpr int ObjectAttempts = 5;  // tries per object or 7z (resumed each time) before an install stops with "try again"
constexpr const char* IssuesUrl = DL_ISSUES_URL;
// D2RLoader is not shipped or downloaded: the player gets it from its official site.
constexpr const char* LoaderSite = DL_LOADER_SITE;
constexpr const char* LoaderVersionDefault = DL_LOADER_VERSION;  // when the manifest names none
// The player's part (docs/downloader.md "How the player starts the mod"). Shown in the window and after an install.
constexpr const char* HowToPlay = DL_HOW_TO_PLAY;

enum class Via { Auto, SevenZip, Objects };  // how files are fetched (docs/downloader.md "7z or objects")

struct Options {
	std::wstring dataDir;    // state, 7z download, log (default %LOCALAPPDATA%\the mod)
	std::string site;        // download site root: https://, http://, file:// or a local folder (default DefaultSite, or the saved one)
	std::string channel;     // "stable" (default) or "test"
	std::wstring gameDir;    // the player's D2R folder (default: the saved one, else detected)
	bool replace = false;    // replace a mods\the mod that the downloader did not make (the window asks first)
	Via via = Via::Auto;
};

struct Progress {
	std::string text;
	double fraction = -1;  // 0..1, or -1 for "busy"
};

// docs/downloader.md "Protected folders": its folders and a classic Diablo II folder are never used.
bool ProtectedFolder(const std::wstring& dir);

class Launcher {
public:
	explicit Launcher(Options o);

	// The state of the D2R folder, for the window (refreshed by Inspect).
	struct View {
		bool d2rFound = false;
		std::wstring d2rDir;
		std::string d2rSource;   // "Battle.net", "Steam", "default folder", "chosen", "saved"
		BuildCheck build;        // against the release's d2r.build (RequiredBuild until the feed is read)
		std::string requiredVersion = RequiredGameVersion;  // "3.3": the release's d2r.version, major.minor
		std::string loaderFound;   // D2RLoader.exe's file version, "" when missing
		std::string loaderNeeded;  // the release's d2rloader.version
		bool loaderOk = false;
		std::string installed;     // installed release ("" none)
		bool foreignMod = false;   // mods\the mod exists, but the downloader did not make it
		bool feedOk = false;
		std::string latest;        // the channel's release
		bool updateAvailable = false;
		// docs/downloader.md "Environment warnings": what D2RLoader really starts. Its game-wide default_mod (checked when D2RLoader or its
		// config is there), and copies of our plugin in the game-wide or another mod's plugins folder.
		bool defaultModChecked = false;
		DefaultModCheck defaultMod;
		std::wstring loaderConfig;  // <D2R>\d2rloader\config\d2rloader.toml
		struct StrayPlugin {
			std::wstring path;
			std::string rel;  // relative to the D2R folder
			PluginPlace place = PluginPlace::None;
			std::string mod;  // OtherMod: its folder
		};
		std::vector<StrayPlugin> strayPlugins;
	};
	View Inspect();
	std::string StatusText(const View& v) const;
	// The environment warnings in full (the CLI's status, the questions before fixing them); empty when all is well.
	std::vector<std::string> EnvironmentWarnings(const View& v) const;
	std::wstring ModDir() const;     // <D2R>\mods\the mod ("" before a D2R folder is known)
	std::wstring D2RDir() const { return d2rDir_; }
	bool SetD2RDir(const std::wstring& dir, std::string& error);  // the player's choice (Browse); saved in the state

	// The actions. Each returns false with a message for the player in `error`.
	bool InstallOrUpdate(std::string& error);  // only what is missing or changed; removes files the release no longer has
	bool Repair(std::string& error);           // the same with every file hashed again
	bool Uninstall(std::string& error);        // mods\the mod only (our marker required); never saves, D2RLoader, other mods
	bool CheckFeed(std::string& error);        // read the channel and the manifest
		// docs/downloader.md "Environment warnings", each after the player said yes. FixDefaultMod sets only default_mod = "<mod>" in D2RLoader's
	// game-wide config (the one write outside mods\the mod); RemoveStrayPlugins moves every copy of our plugin outside
	// mods\the mod into <data>\removed\<date>\ (never deleted).
	bool FixDefaultMod(std::string& error);
	bool RemoveStrayPlugins(std::string& error);
	bool ForeignModFolder() const { return foreign_; }  // set by an install that stopped to ask
	void SetReplace(bool replace) { opt_.replace = replace; }  // the player said yes

	// Self-update (docs/downloader.md "Self-update"): the manifest's `launcher` entry, newer than this one, after a
	// CheckFeed (also one that refused the release because it needs a newer downloader).
	bool LauncherUpdateAvailable() const;
	std::string LauncherUpdateVersion() const { return offer_.version; }
	// Downloads it next to this exe, checks size and sha256 against the manifest (whether or not it is signed), then
	// swaps the files: this exe becomes <exe>.old, the new one takes its name. The caller starts it.
	bool SelfUpdate(std::string& error);
	static std::wstring ExePath();
	static void RemoveOldExe();  // <exe>.old of an earlier update, once that process has ended

	std::wstring SavesDir();
	const Options& options() const { return opt_; }

	std::function<void(const Progress&)> onProgress;
	std::atomic<bool> cancel{false};

private:
	bool FindD2R(std::string& error);
	bool PrepareTarget(std::string& error);  // marker, writable, not running
	bool EnsureMod(bool verify, std::string& error);
	bool LoadState();
	bool SaveState();
	void Report(const std::string& text, double fraction = -1);
	std::string ModFolder() const;
	bool InstallObjects(const std::wstring& mod, std::vector<ManifestFile>& fetch, Json& files, std::string& error);
	bool InstallFrom7z(const std::wstring& mod, std::vector<ManifestFile>& fetch, Json& files, std::string& error);
	bool Download7z(std::wstring& file, std::string& error);
	bool UseSevenZip(const std::vector<ManifestFile>& fetch, uint64_t localBytes) const;
	std::string NewLauncherUrl() const;

	Options opt_;
	std::wstring statePath_;
	Json state_;
	std::wstring d2rDir_;
	std::string d2rSource_;
	bool foreign_ = false;
	bool feedChecked_ = false;
	Manifest feed_;            // the channel's release manifest
	decltype(Manifest::launcher) offer_;  // its launcher entry (set even when the release itself is refused)
	mutable std::string selfSha256_;      // sha256 of this exe, read once: an offer of the same bytes is no update
	std::string siteRoot_;     // SiteRoot(opt_.site)
};

}  // namespace dl
