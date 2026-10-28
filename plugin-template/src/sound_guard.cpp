// Sound guard (sound_guard.h). Build 93847 only; every entry was checked against a dump of that image and the hooks
// were run in game.
//
// Every function of the image that turns a sound id into a sounds.txt record (callers of the row count 0x3b20c0 and the
// record getter 0x3b1fd0) checks the id against the row count but then goes on with a null record. Five of them read
// through it; each is hooked at its entry, and an id outside the table returns what the function returns for an empty
// table:
//   0xd8c70 (id) -> record +0xc7          returns 0
//   0xd9180 (id) -> record +0xb0 & mask   returns 0
//   0xd9030 (id, buffer, size)            the file path into buffer; returns an empty path
//   0xd9e60 (id, ?, ?) -> id to play      returns the id itself
//   0xda360 (id, mode) -> group variant   returns the id itself
// The others need nothing: 0xd9210 / 0xd92f0 test the record for null, 0xd96a0 returns null, 0xdb700 / 0xdb8f0 /
// 0x1fd600 walk all rows. Guarding only the first caller (the unit sound wrapper 0x107dd0) was not enough: the game then
// crashed in 0xda360.
//
// The table check runs after each completed table load (D2RLoader's lifecycle service, DataTablesLoaded): fewer rows
// than the mod's means this start loaded D2R's own table (the cache-building start), so the log tells the player to
// close the game and start it again.
#include "sound_guard.h"

#include "log.h"

#include <intrin.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {

constexpr uint64_t SoundCountRva = 0x3b20c0;  // () -> sounds.txt row count

// Entry bytes: whole instructions, no rip-relative operand.
constexpr uint64_t FieldC7Rva = 0xd8c70;
constexpr uint8_t FieldC7Entry[] = {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xd9};  // push rbx / sub rsp,0x20 / mov ebx,ecx
constexpr uint64_t FieldB0Rva = 0xd9180;
constexpr uint8_t FieldB0Entry[] = {0x40, 0x53, 0x48, 0x83, 0xec, 0x20, 0x8b, 0xd9};  // same
constexpr uint64_t PathRva = 0xd9030;
constexpr uint8_t PathEntry[] = {0x40, 0x53, 0x55, 0x57, 0x48, 0x83, 0xec, 0x20};  // push rbx/rbp/rdi / sub rsp,0x20
constexpr uint64_t PickRva = 0xd9e60;
constexpr uint8_t PickEntry[] = {0x40, 0x55, 0x53, 0x41, 0x54, 0x41, 0x55, 0x48, 0x8d, 0x6c, 0x24, 0xf8};  // push rbp/rbx/r12/r13 / lea rbp,[rsp-8]
constexpr uint64_t VariantRva = 0xda360;
constexpr uint8_t VariantEntry[] = {0x40, 0x53, 0x55, 0x56, 0x57, 0x41, 0x55, 0x48, 0x83, 0xec, 0x20};  // push rbx/rbp/rsi/rdi/r13 / sub rsp,0x20

// Four integer-register arguments cover every reader; unused ones are passed through untouched.
using ReaderFn = uint64_t (*)(int id, uint64_t a, uint64_t b, uint64_t c) noexcept;
using SoundCountFn = int (*)() noexcept;

SoundCountFn g_count = nullptr;
uint64_t g_base = 0;
int g_modRows = 0;
std::atomic<uint32_t> g_skipped{0};
std::atomic<int> g_lastId{0};
std::atomic<uint64_t> g_lastSite{0};
std::atomic<uint64_t> g_lastCaller{0};
ReaderFn g_fieldC7 = nullptr;
ReaderFn g_fieldB0 = nullptr;
ReaderFn g_path = nullptr;
ReaderFn g_pick = nullptr;
ReaderFn g_variant = nullptr;

// True (counted, sampled into the log) when `id` is not a row of the loaded sounds table.
auto Skip(int id, uint64_t site, uint64_t caller) noexcept -> bool {
	const int rows = g_count();
	if (id >= 0 && id < rows) {
		return false;
	}
	g_lastId = id;
	g_lastSite = site;
	g_lastCaller = caller;
	if (plog::Sample(g_skipped)) {
		plog::Warn("bad sound id %d (table has %d rows): 0x%llx returned the empty answer, called from +0x%llx (%u times)", id, rows,
		           static_cast<unsigned long long>(site), static_cast<unsigned long long>(caller), g_skipped.load());
	}
	return true;
}

auto Caller(void* returnAddress) noexcept -> uint64_t {
	return reinterpret_cast<uint64_t>(returnAddress) - g_base;
}

uint64_t FieldC7Hook(int id, uint64_t a, uint64_t b, uint64_t c) noexcept {
	return Skip(id, FieldC7Rva, Caller(_ReturnAddress())) ? 0 : g_fieldC7(id, a, b, c);
}

uint64_t FieldB0Hook(int id, uint64_t a, uint64_t b, uint64_t c) noexcept {
	return Skip(id, FieldB0Rva, Caller(_ReturnAddress())) ? 0 : g_fieldB0(id, a, b, c);
}

uint64_t PathHook(int id, uint64_t buffer, uint64_t size, uint64_t c) noexcept {
	if (!Skip(id, PathRva, Caller(_ReturnAddress()))) {
		return g_path(id, buffer, size, c);
	}
	if (buffer != 0 && size != 0) {
		*reinterpret_cast<char*>(buffer) = '\0';
	}
	return 0;
}

uint64_t PickHook(int id, uint64_t a, uint64_t b, uint64_t c) noexcept {
	return Skip(id, PickRva, Caller(_ReturnAddress())) ? static_cast<uint32_t>(id) : g_pick(id, a, b, c);
}

uint64_t VariantHook(int id, uint64_t a, uint64_t b, uint64_t c) noexcept {
	return Skip(id, VariantRva, Caller(_ReturnAddress())) ? static_cast<uint32_t>(id) : g_variant(id, a, b, c);
}

// Game thread, after each completed table load (also reloads).
void __cdecl OnDataTablesLoaded(const D2RL::PluginContext*, const D2RL::Lifecycle::DataTablesLoadedEvent*, void*) noexcept {
	const int rows = g_count();
	if (rows <= 0 || g_modRows <= 0) {
		return;
	}
	if (rows >= g_modRows) {
		plog::Info("sounds table %d rows (mod %d), ok", rows, g_modRows);
		return;
	}
	plog::Warn("sounds.txt has %d rows, the mod's has %d: this start loaded D2R's own sounds (D2RLoader's cache-building "
	           "start after an install or update), so the mod's new sounds are silent. Please close the game and start it "
	           "again.",
	           rows, g_modRows);
}

}  // namespace

auto InstallSoundGuard(const D2RL::PluginContext* context, int modSoundRows, char* report, size_t reportSize) noexcept -> bool {
	g_base = context->exeBase;
	g_modRows = modSoundRows;
	g_count = reinterpret_cast<SoundCountFn>(g_base + SoundCountRva);
	struct Reader {
		uint64_t rva;
		const uint8_t* entry;
		uint32_t size;
		ReaderFn hook;
		ReaderFn* original;
	};
	const Reader readers[] = {
	    {FieldC7Rva, FieldC7Entry, sizeof(FieldC7Entry), &FieldC7Hook, &g_fieldC7},
	    {FieldB0Rva, FieldB0Entry, sizeof(FieldB0Entry), &FieldB0Hook, &g_fieldB0},
	    {PathRva, PathEntry, sizeof(PathEntry), &PathHook, &g_path},
	    {PickRva, PickEntry, sizeof(PickEntry), &PickHook, &g_pick},
	    {VariantRva, VariantEntry, sizeof(VariantEntry), &VariantHook, &g_variant},
	};
	constexpr int Total = static_cast<int>(sizeof(readers) / sizeof(readers[0]));
	int installed = 0;
	char failed[96] = "";
	for (const Reader& r : readers) {
		if (context->InstallInlineHook(r.rva, r.entry, r.size, r.hook, r.original)) {
			++installed;
		} else {
			const size_t used = std::strlen(failed);
			std::snprintf(failed + used, sizeof(failed) - used, " 0x%llx", static_cast<unsigned long long>(r.rva));
		}
	}
	const D2RL::LifecycleService* lifecycle = nullptr;
	bool listening = false;
	if (context->QueryService(&lifecycle) == D2RL::ServiceQueryResult::Success && lifecycle != nullptr &&
	    D2RL::HasLifecycleServiceField(lifecycle, D2RL::LifecycleServiceRequiredSize)) {
		const D2RL::Lifecycle::DataTablesLoadedListener listener{D2RL::Lifecycle::DataTablesLoadedListenerSize, 0, OnDataTablesLoaded, nullptr};
		D2RL::Lifecycle::ListenerHandle handle = D2RL::Lifecycle::InvalidHandle;
		listening = lifecycle->registerDataTablesLoadedListener(context, &listener, &handle) == D2RL::Lifecycle::Result::Success &&
		            handle != D2RL::Lifecycle::InvalidHandle;
	}
	std::snprintf(report, reportSize, "%s: record readers %d of %d hooked%s%s%s; table check %s", installed == Total ? "on" : "partial",
	              installed, Total, failed[0] != '\0' ? " (refused:" : "", failed, failed[0] != '\0' ? ")" : "",
	              listening ? "after each table load" : "OFF (no lifecycle listener)");
	return installed == Total;
}

void SoundGuardStatus(char* out, size_t size) noexcept {
	const int rows = g_count != nullptr ? g_count() : 0;
	std::snprintf(out, size, "sounds.txt %d rows (mod %d)%s, %u bad sound ids skipped (last id %d in 0x%llx from +0x%llx)", rows, g_modRows,
	              g_modRows > 0 && rows > 0 && rows < g_modRows ? " NOT the mod's table: restart the game" : "", g_skipped.load(),
	              g_lastId.load(), static_cast<unsigned long long>(g_lastSite.load()), static_cast<unsigned long long>(g_lastCaller.load()));
}
