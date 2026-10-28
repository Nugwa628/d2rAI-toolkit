// Entry point. Without arguments it opens the window (window.cpp); with --cli it runs one action in a console and
// exits, which is what the tests use (docs/downloader.md "The command line").
#include "app.h"
#include "../core/config.h"
#include "../core/paths.h"
#include "game.h"
#include "http.h"
#include "util.h"
#include "window.h"

#include <windows.h>
#include <shellapi.h>


using namespace dl;

namespace {

const wchar_t* Usage =
	DL_W(DL_EXE_NAME) L".exe [--cli status|detect|install|update|repair|uninstall|self-update|fix-default-mod|remove-stray-plugins] [options]\n"
	L"  --data-dir DIR   where the state, downloads and log go (default %LOCALAPPDATA%\\" DL_W(DL_STATE_FOLDER) L")\n"
	L"  --site URL|DIR   root of the download site (default " DL_W(DL_SITE_URL) L"; also file:// or a local folder, for testing)\n"
	L"  --channel NAME   stable (default) or test\n"
	L"  --game-dir DIR   the D2R install to use (default: detected from Battle.net / Steam); the mod goes to DIR\\mods\\" DL_W(DL_MOD_FOLDER) L"\n"
	L"  --replace        overwrite a mods\\" DL_W(DL_MOD_FOLDER) L" that this downloader did not create\n"
	L"  --via auto|7z|objects  download method (auto: the 7z for a new install, single objects for updates)\n"
	L"  --snapshot FILE  render the window to a PNG, then quit\n";

struct Cli {
	std::wstring command;
	std::wstring snapshot;
	Options opt;
	bool bad = false;
};

Cli ParseArgs() {
	Cli c;
	int argc = 0;
	LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
	for (int i = 1; i < argc; ++i) {
		std::wstring a = argv[i];
		auto next = [&]() -> std::wstring {
			if (i + 1 < argc)
				return argv[++i];
			c.bad = true;
			return L"";
		};
		if (a == L"--cli") c.command = next();
		else if (a == L"--data-dir") c.opt.dataDir = next();
		else if (a == L"--site") c.opt.site = Utf8(next());
		else if (a == L"--channel") c.opt.channel = Utf8(next());
		else if (a == L"--game-dir") c.opt.gameDir = next();
		else if (a == L"--replace") c.opt.replace = true;
		else if (a == L"--via") {
			std::wstring v = next();
			if (v == L"auto") c.opt.via = Via::Auto;
			else if (v == L"7z") c.opt.via = Via::SevenZip;
			else if (v == L"objects") c.opt.via = Via::Objects;
			else c.bad = true;
		}
		else if (a == L"--snapshot") c.snapshot = next();
		else c.bad = true;
	}
	LocalFree(argv);
	return c;
}

void SetupLog(const Options& o, bool console) {
	std::wstring data = o.dataDir.empty() ? Wide(DefaultDataDir(Utf8(KnownFolder(0)))) : o.dataDir;
	if (ProtectedFolder(data))  // never write into another launcher's folders (the downloader refuses them)
		data = Wide(DefaultDataDir(Utf8(KnownFolder(0))));
	LogInit(JoinPath(data, L"downloader.log"), console);
}

int RunCli(Cli& c) {
	if (!AttachConsole(ATTACH_PARENT_PROCESS))
		AllocConsole();
	SetupLog(c.opt, true);
	Log(std::string(DL_PRODUCT_TITLE " ") + LauncherVersion + " --cli " + Utf8(c.command));
	if (c.command == L"detect") {
		auto found = FindD2RInstalls();
		if (found.empty())
			Log("found no D2R install");
		for (const auto& d : found)
			Log(Utf8(d.dir) + " | " + d.source + " | .build.info " + d.infoVersion + " | D2R.exe " + d.exeVersion + " | " + d.build.message);
		return found.empty() ? 1 : 0;
	}
	Launcher l(c.opt);
	std::string last;
	l.onProgress = [&](const Progress& p) {
		if (p.fraction >= 0 && p.text != last) {  // Report() already logged the plain messages
			static ULONGLONG t = 0;
			if (GetTickCount64() - t > 3000) {
				t = GetTickCount64();
				Log(p.text);
			}
		}
		last = p.text;
	};
	std::string err;
	bool ok = true;
	if (c.command == L"status") {
		l.CheckFeed(err);
		Launcher::View v = l.Inspect();
		Log(l.StatusText(v));
		for (const std::string& w : l.EnvironmentWarnings(v))
			Log("WARNING: " + w);
		if (v.defaultModChecked && !v.defaultMod.ok)
			Log("  fix: --cli fix-default-mod (writes only default_mod = \"" DL_MOD_FOLDER "\" to D2RLoader's config)");
		if (!v.strayPlugins.empty())
			Log("  fix: --cli remove-stray-plugins (moves them to " + Utf8(l.options().dataDir) + R"(\removed\<date>\, deletes nothing))");
		if (!err.empty())
			Log("feed: " + err);
		return 0;
	}
	if (c.command == L"install" || c.command == L"update") ok = l.InstallOrUpdate(err);
	else if (c.command == L"repair") ok = l.Repair(err);
	else if (c.command == L"uninstall") ok = l.Uninstall(err);
	else if (c.command == L"fix-default-mod") ok = l.FixDefaultMod(err);
	else if (c.command == L"remove-stray-plugins") ok = l.RemoveStrayPlugins(err);
	else if (c.command == L"self-update") {
		std::string feedErr;
		if (!l.CheckFeed(feedErr) && !l.LauncherUpdateAvailable())
			Log("feed: " + feedErr);
		ok = l.SelfUpdate(err);
	}
	else {
		Log(Utf8(Usage));
		return 2;
	}
	Log(ok ? "OK" : "FAILED: " + err);
	return ok ? 0 : 1;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
	SetUserAgent(DL_W(DL_EXE_NAME) L"/" + Wide(LauncherVersion));
	Launcher::RemoveOldExe();
	Cli c = ParseArgs();
	if (c.bad) {
		MessageBoxW(nullptr, Usage, DL_W(DL_PRODUCT_TITLE), MB_ICONINFORMATION);
		return 2;
	}
	if (!c.command.empty())
		return RunCli(c);
	SetupLog(c.opt, false);
	Log(std::string(DL_PRODUCT_TITLE " ") + LauncherVersion + " (window)");
	return RunWindow(inst, c.opt, c.snapshot);
}
