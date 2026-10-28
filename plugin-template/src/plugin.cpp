// D2RLoader plugin entry point (template). Copy plugin-template/ into your mod project and extend it.
//
// Target: D2R 3.3.0 build 93847 under D2RLoader 1.3.1, PluginSDK 0.3.0.
//
// What it does at load:
//  - declares itself Shared (server + client behaviour), ModScopedOnly (loads only from
//    <game>/mods/<Mod>/d2rloader/plugins/ while that mod runs) and NativeHooks (patches game code);
//  - log.h: one log line for the plugin (version, D2R version, image stamp, mod, this DLL's path) and one per part;
//  - build_guard.h: checks the game image (PE TimeDateStamp). Every RVA belongs to one image; on any other image the
//    engine parts stay off and say so, so a D2R/D2RLoader update cannot make the plugin patch random code;
//  - off_switch.h: reads "<plugin>-off.txt" next to the DLL: words naming parts to leave out (bisecting a crash);
//  - env_guard.h: warns when D2RLoader runs another mod, when this DLL is not in the mod's own folder, or when a second
//    copy of it is loaded; names the other plugins (no game address: runs even on a wrong image);
//  - installs each part and logs one line per part;
//  - registers the console command "<plugin>" that prints the state of every part.
//
// Adding a part: write Install<Part>(context, report, size) in its own file, add a row to g_parts with a one-word name
// (the word the off-file uses) and whether it needs the expected image. It shows up in the log and the status command.
#include <D2RLPlugin/api.h>

#include "build_guard.h"
#include "d2r.h"
#include "env_guard.h"
#include "hook_example.h"
#include "item_type_check.h"
#include "log.h"
#include "off_switch.h"
#include "skill_functions.h"
#include "sound_guard.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

#ifndef MODKIT_PLUGIN_NAME
#define MODKIT_PLUGIN_NAME "mod-core"
#endif
#ifndef MODKIT_PLUGIN_VERSION
#define MODKIT_PLUGIN_VERSION "0.1.0"
#endif
#ifndef MODKIT_MOD_NAME
#define MODKIT_MOD_NAME ""  // the mod folder this plugin ships in (mods\<name>); "" skips the mod checks
#endif
#ifndef MODKIT_SOUND_ROWS
#define MODKIT_SOUND_ROWS 0  // rows of the mod's sounds.txt; 0 = the mod adds no sounds (no restart warning)
#endif

namespace {

constexpr const char* PluginName = MODKIT_PLUGIN_NAME;
constexpr const char* PluginVersion = MODKIT_PLUGIN_VERSION;

const D2RL::PluginContext* g_context = nullptr;
uint32_t g_stamp = 0;
char g_selfPath[520] = "?";

constexpr D2RL::PluginInfo Info {
	.infoSize    = D2RL::PluginInfoSize,
	.abiVersion  = D2RL_PLUGIN_ABI_VERSION,
	.id          = MODKIT_PLUGIN_NAME,
	.name        = MODKIT_PLUGIN_NAME,
	.version     = MODKIT_PLUGIN_VERSION,
	.author      = "d2r-modkit",
	.description = "Engine changes for this mod (skill functions, hooks, guards).",
	.flags       = D2RL::PluginFlags::Shared | D2RL::PluginFlags::ModScopedOnly | D2RL::PluginFlags::NativeHooks,
};

// --- Parts --------------------------------------------------------------------------------------------------------
using InstallFn = void (*)(const D2RL::PluginContext* context, char* report, size_t size) noexcept;

struct Part {
	const char* word;   // off-file word and status label
	bool engine;        // uses game addresses: installed only on the expected image
	InstallFn install;
	char report[512];
};

void InstallSkills(const D2RL::PluginContext* context, char* report, size_t size) noexcept {
	char slots[400];
	const int n = InstallSkillFunctions(context, slots, sizeof(slots));
	std::snprintf(report, size, "%d/%d installed:%s", n, SkillFunctionCount(), slots);
}

void InstallHook(const D2RL::PluginContext* context, char* report, size_t size) noexcept {
	(void)InstallHookExample(context, report, size);
}

void InstallSounds(const D2RL::PluginContext* context, char* report, size_t size) noexcept {
	(void)InstallSoundGuard(context, MODKIT_SOUND_ROWS, report, size);
}

void InstallTypeIds(const D2RL::PluginContext* context, char* report, size_t size) noexcept {
	(void)CheckItemTypeIds(context, report, size);
}

void InstallEnvironment(const D2RL::PluginContext* context, char* report, size_t size) noexcept {
	StartEnvironmentGuard(context, MODKIT_MOD_NAME);
	EnvironmentStatus(report, size);
}

Part g_parts[] = {
	{"envguard", false, InstallEnvironment, ""},
	{"soundguard", true, InstallSounds, ""},
	{"typeids", true, InstallTypeIds, ""},
	{"skills", true, InstallSkills, ""},
	{"hook", true, InstallHook, ""},
};

void InstallParts(const D2RL::PluginContext* context, bool imageOk) noexcept {
	for (Part& p : g_parts) {
		if (off_switch::Off(p.word)) {
			std::snprintf(p.report, sizeof(p.report), "off: left out by %s-off.txt", PluginName);
		} else if (p.engine && !imageOk) {
			std::snprintf(p.report, sizeof(p.report), "off: game image differs from the one this plugin was built for");
		} else {
			p.install(context, p.report, sizeof(p.report));
		}
		plog::Info("%s %s", p.word, p.report);
	}
}

// --- Console command ----------------------------------------------------------------------------------------------
auto StatusCommand(D2R::Game::Client* client, const D2RL::ConsoleCommandContext* command, void* userData) noexcept
	-> D2RL::ConsoleCommandResult {
	(void)client;
	(void)userData;
	if (command == nullptr || command->plugin == nullptr || g_context == nullptr) {
		return D2RL::ConsoleCommandResult::Failed;
	}
	char line[1100];
	char image[200];
	build_guard::Describe(image, sizeof(image), g_stamp, g_context->buildVersion);
	std::snprintf(line, sizeof(line), "%s %s | %s | mod %s | file %s", PluginName, PluginVersion, image,
	              g_context->activeMod != nullptr ? g_context->activeMod : "?", g_selfPath);
	command->plugin->WriteConsoleMessage(line);
	if (off_switch::Words()[0] != '\0') {
		std::snprintf(line, sizeof(line), "off-file: %s", off_switch::Words());
		command->plugin->WriteConsoleMessage(line);
	}
	for (const Part& p : g_parts) {
		// live state where the part has one, else its install report
		char state[sizeof(p.report) + 64];
		if (std::strcmp(p.word, "envguard") == 0 && !off_switch::Off(p.word)) {
			EnvironmentStatus(state, sizeof(state));
		} else if (std::strcmp(p.word, "soundguard") == 0 && std::strncmp(p.report, "off", 3) != 0) {
			char live[300];
			SoundGuardStatus(live, sizeof(live));
			std::snprintf(state, sizeof(state), "%s | %s", p.report, live);
		} else if (std::strcmp(p.word, "hook") == 0 && std::strncmp(p.report, "on", 2) == 0) {
			std::snprintf(state, sizeof(state), "%s (%lld calls)", p.report, static_cast<long long>(HookExampleCalls()));
		} else {
			std::snprintf(state, sizeof(state), "%s", p.report);
		}
		std::snprintf(line, sizeof(line), "%s %s", p.word, state);
		command->plugin->WriteConsoleMessage(line);
	}
	OtherPlugins(line, sizeof(line));
	command->plugin->WriteConsoleMessage(line);
	plog::Info("%s", line);  // also in the plugin log, for crash reports
	return D2RL::ConsoleCommandResult::Handled;
}

}  // namespace

D2RL_PLUGIN_EXPORT auto D2RLoaderGetPluginInfo() noexcept -> const D2RL::PluginInfo* {
	return &Info;
}

D2RL_PLUGIN_EXPORT auto D2RLoaderLoadPlugin(const D2RL::PluginContext* context) noexcept -> bool {
	if (context == nullptr) {
		return false;
	}
	g_context = context;
	plog::Init(context, PluginName);
	g_stamp = build_guard::ImageStamp(context->exeBase);
	const bool imageOk = build_guard::Matches(g_stamp);
	if (context->pluginPath != nullptr) {
		std::snprintf(g_selfPath, sizeof(g_selfPath), "%ls", context->pluginPath);
	}
	char image[200];
	build_guard::Describe(image, sizeof(image), g_stamp, context->buildVersion);
	plog::Info("%s loaded (mod %s, %s, file %s)", PluginVersion, context->activeMod != nullptr ? context->activeMod : "?", image, g_selfPath);
	off_switch::Read();
	if (off_switch::Words()[0] != '\0') {
		plog::Info("%s-off.txt leaves out: %s", PluginName, off_switch::Words());
	}
	if (imageOk) {
		d2r::Bind(context->exeBase);
	} else {
		plog::Warn("not the expected game image: every engine part stays off");
	}
	InstallParts(context, imageOk);
	return context->RegisterConsoleCommand(PluginName, StatusCommand, "Status of this mod's engine plugin and its guards.");
}

D2RL_PLUGIN_EXPORT void D2RLoaderUnloadPlugin() noexcept {
	g_context = nullptr;
}
