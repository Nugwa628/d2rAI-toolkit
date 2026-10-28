// Environment guard (env_guard.h): which copy of this plugin runs, from where, for which mod, next to which plugins.
#include "env_guard.h"

#include "log.h"

#define NOMINMAX
#include <windows.h>
// tlhelp32.h needs windows.h first
#include <tlhelp32.h>

#include <cstddef>
#include <cstdio>
#include <cstring>
#include <mutex>

namespace {

const D2RL::PluginContext* g_context = nullptr;
char g_expectedMod[128] = "";
char g_selfPath[MAX_PATH * 3] = "";
char g_report[1024] = "not checked yet";
std::mutex g_lock;

auto Lower(char c) noexcept -> char {
	return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : (c == '/' ? '\\' : c);
}

auto EqualNoCase(const char* a, const char* b) noexcept -> bool {
	for (; *a != '\0' && *b != '\0'; ++a, ++b) {
		if (Lower(*a) != Lower(*b)) {
			return false;
		}
	}
	return *a == '\0' && *b == '\0';
}

// Offset of "\d2rloader\plugins\" in `path` (any case, '\' or '/'), or -1.
auto PluginDirAt(const char* path) noexcept -> int {
	static constexpr char Needle[] = "\\d2rloader\\plugins\\";
	constexpr size_t n = sizeof(Needle) - 1;
	for (size_t i = 0; path[i] != '\0'; ++i) {
		size_t k = 0;
		while (k < n && path[i + k] != '\0' && Lower(path[i + k]) == Needle[k]) {
			++k;
		}
		if (k == n) {
			return static_cast<int>(i);
		}
	}
	return -1;
}

// "mod <name>" when `path` is ...\mods\<name>\d2rloader\plugins\..., else "game-wide".
void PlaceOf(const char* path, char* out, size_t size) noexcept {
	std::snprintf(out, size, "game-wide");
	const int at = PluginDirAt(path);
	if (at <= 0) {
		return;
	}
	int start = at - 1;  // walk back over <name>
	while (start >= 0 && path[start] != '\\' && path[start] != '/') {
		--start;
	}
	const int nameStart = start + 1;
	const int nameLen = at - nameStart;
	if (start < 5 || nameLen <= 0) {
		return;
	}
	char mods[7] = {};
	for (int i = 0; i < 6; ++i) {
		mods[i] = Lower(path[start - 5 + i]);
	}
	if (std::strcmp(mods, "\\mods\\") == 0) {
		std::snprintf(out, size, "mod %.*s", nameLen, path + nameStart);
	}
}

auto Utf8(const wchar_t* w, char* out, size_t size) noexcept -> bool {
	out[0] = '\0';
	return w != nullptr && WideCharToMultiByte(CP_UTF8, 0, w, -1, out, static_cast<int>(size), nullptr, nullptr) > 0;
}

auto SelfModule() noexcept -> HMODULE {
	HMODULE self = nullptr;
	GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
	                   reinterpret_cast<LPCWSTR>(&SelfModule), &self);
	return self;
}

// The PluginInfo id of a loaded D2RLoader plugin module, or null.
auto PluginIdOf(HMODULE module) noexcept -> const char* {
	using GetInfo = const D2RL::PluginInfo* (*)();
	auto get = reinterpret_cast<GetInfo>(reinterpret_cast<void*>(GetProcAddress(module, "D2RLoaderGetPluginInfo")));
	if (get == nullptr) {
		return nullptr;
	}
	const D2RL::PluginInfo* info = get();
	return info != nullptr && info->infoSize >= offsetof(D2RL::PluginInfo, id) + sizeof(info->id) ? info->id : nullptr;
}

// Calls fn(path, module) for every other module of this process under a ...\d2rloader\plugins\ folder.
template <typename Fn>
auto ForEachOtherPlugin(Fn fn) noexcept -> bool {
	const HMODULE self = SelfModule();
	HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
	if (snap == INVALID_HANDLE_VALUE) {
		return false;
	}
	MODULEENTRY32W me{};
	me.dwSize = sizeof(me);
	for (BOOL ok = Module32FirstW(snap, &me); ok; ok = Module32NextW(snap, &me)) {
		char path[MAX_PATH * 3];
		if (me.hModule == self || !Utf8(me.szExePath, path, sizeof(path)) || PluginDirAt(path) < 0) {
			continue;
		}
		fn(path, me.hModule);
	}
	CloseHandle(snap);
	return true;
}

void Check(bool atLoad) noexcept {
	if (g_context == nullptr) {
		return;
	}
	char report[sizeof(g_report)] = "";
	size_t len = 0;
	auto add = [&](const char* text) {
		if (len < sizeof(report)) {
			const int w = std::snprintf(report + len, sizeof(report) - len, "%s%s", len != 0 ? "; " : "", text);
			len += w > 0 ? static_cast<size_t>(w) : 0;
		}
	};
	char line[MAX_PATH * 3 + 160];
	const char* active = g_context->activeMod;
	if (g_expectedMod[0] != '\0' && active != nullptr && active[0] != '\0' && !EqualNoCase(active, g_expectedMod)) {
		std::snprintf(line, sizeof(line), "D2RLoader runs the mod %s, not %s (default_mod in <game>\\d2rloader\\config\\d2rloader.toml)",
		              active, g_expectedMod);
		add(line);
	}
	if (g_expectedMod[0] != '\0' && g_selfPath[0] != '\0') {
		char place[160];
		char want[160];
		PlaceOf(g_selfPath, place, sizeof(place));
		std::snprintf(want, sizeof(want), "mod %s", g_expectedMod);
		if (!EqualNoCase(place, want)) {
			std::snprintf(line, sizeof(line), "this plugin is loaded from %s (%s), not from mods\\%s", g_selfPath, place, g_expectedMod);
			add(line);
		}
	}
	const char* ourId = g_context->pluginId;
	ForEachOtherPlugin([&](const char* path, HMODULE module) {
		const char* id = PluginIdOf(module);
		if (id != nullptr && ourId != nullptr && std::strcmp(id, ourId) == 0) {
			std::snprintf(line, sizeof(line), "a second copy of this plugin is loaded: %s", path);
			add(line);
		}
	});
	std::lock_guard<std::mutex> hold(g_lock);
	if (len == 0) {
		std::snprintf(g_report, sizeof(g_report), "ok%s", atLoad ? " (checked again once the game-wide plugins have loaded)" : "");
		if (!atLoad) {
			plog::Info("environment ok");
		}
		return;
	}
	std::snprintf(g_report, sizeof(g_report), "WARNING: %s. What runs may not be this build of the mod", report);
	plog::Warn("environment %s", g_report);
}

DWORD WINAPI CheckLater(LPVOID) {
	Sleep(15000);
	Check(false);
	char line[512];
	OtherPlugins(line, sizeof(line));
	plog::Info("%s", line);
	return 0;
}

}  // namespace

void StartEnvironmentGuard(const D2RL::PluginContext* context, const char* expectedMod) noexcept {
	g_context = context;
	std::snprintf(g_expectedMod, sizeof(g_expectedMod), "%s", expectedMod != nullptr ? expectedMod : "");
	wchar_t self[MAX_PATH * 2] = L"";
	if (GetModuleFileNameW(SelfModule(), self, static_cast<DWORD>(sizeof(self) / sizeof(self[0]))) == 0 ||
	    !Utf8(self, g_selfPath, sizeof(g_selfPath))) {
		g_selfPath[0] = '\0';
	}
	Check(true);
	if (HANDLE t = CreateThread(nullptr, 0, CheckLater, nullptr, 0, nullptr)) {
		CloseHandle(t);
	}
}

void EnvironmentStatus(char* out, size_t size) noexcept {
	std::lock_guard<std::mutex> hold(g_lock);
	std::snprintf(out, size, "%s", g_report);
}

void OtherPlugins(char* out, size_t size) noexcept {
	int head = std::snprintf(out, size, "other plugins:");
	size_t len = head > 0 ? static_cast<size_t>(head) : 0;
	int found = 0;
	const bool ok = ForEachOtherPlugin([&](const char* path, HMODULE) {
		const char* slash = std::strrchr(path, '\\');
		char place[160];
		PlaceOf(path, place, sizeof(place));
		if (len < size) {
			const int w = std::snprintf(out + len, size - len, "%s %s (%s)", found != 0 ? "," : "", slash != nullptr ? slash + 1 : path, place);
			len += w > 0 ? static_cast<size_t>(w) : 0;
		}
		++found;
	});
	if (!ok) {
		std::snprintf(out, size, "other plugins: unknown (could not list the modules, error %lu)", GetLastError());
	} else if (found == 0) {
		std::snprintf(out, size, "other plugins: none");
	}
}
