// Item type id self-check (item_type_check.h). Sites: every call of 0x373890 in D2R.exe 93847 whose type argument is
// an immediate >= 67 (knowledge/d2r-93847-addresses.md "Item type test sites").
#include "item_type_check.h"

#include "item_type_ids.h"
#include "log.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

namespace {

enum class Form : uint8_t { MovEdx, LeaEdx };  // `mov edx, imm32` (ba xx 00 00 00) / `lea edx, [reg + imm8]` (8d 5x xx)

struct Site {
	uint64_t rva;  // the instruction that loads the type into edx before the call
	Form form;
	uint8_t modrm;  // lea only: 0x50 = [rax + imm8], 0x51 = [rcx + imm8]
	int d2rId;
	const char* code;
};

constexpr Site Sites[] = {
	// h2h (68)
	{0x3854f9, Form::MovEdx, 0, 68, "h2h"},
	// pelt (73)
	{0x40bbef, Form::LeaEdx, 0x50, 73, "pelt"},
	// rune (75)
	{0x0cc192, Form::MovEdx, 0, 75, "rune"},
	{0x1c85a4, Form::MovEdx, 0, 75, "rune"},
	{0x23165a, Form::MovEdx, 0, 75, "rune"},
	{0x2bd438, Form::MovEdx, 0, 75, "rune"},
	{0x2c2934, Form::MovEdx, 0, 75, "rune"},
	{0x2d9dc2, Form::MovEdx, 0, 75, "rune"},
	{0x43c979, Form::MovEdx, 0, 75, "rune"},
	{0x43cc33, Form::MovEdx, 0, 75, "rune"},
	{0x475283, Form::MovEdx, 0, 75, "rune"},
	{0x527f5a, Form::MovEdx, 0, 75, "rune"},
	// hpot (77)
	{0x15a026, Form::MovEdx, 0, 77, "hpot"},
	{0x2296d7, Form::MovEdx, 0, 77, "hpot"},
	{0x4f1e0e, Form::MovEdx, 0, 77, "hpot"},
	{0x4f4d1a, Form::MovEdx, 0, 77, "hpot"},
	// apot (81)
	{0x15a037, Form::LeaEdx, 0x50, 81, "apot"},
	{0x2296f4, Form::LeaEdx, 0x50, 81, "apot"},
	{0x4f4d3c, Form::LeaEdx, 0x50, 81, "apot"},
	// wpot (82)
	{0x15a046, Form::LeaEdx, 0x50, 82, "wpot"},
	{0x2296e5, Form::LeaEdx, 0x50, 82, "wpot"},
	{0x4f4d2c, Form::LeaEdx, 0x50, 82, "wpot"},
	// scha (83)
	{0x422fec, Form::LeaEdx, 0x51, 83, "scha"},
	// mcha (84)
	{0x422ff7, Form::MovEdx, 0, 84, "mcha"},
	// gem4 (96)
	{0x527eff, Form::MovEdx, 0, 96, "gem4"},
};

auto ModId(const char* code) noexcept -> int {
	for (const auto& t : item_type_ids::Mod) {
		if (std::strcmp(t.code, code) == 0) {
			return t.id;
		}
	}
	return -1;
}

// The instruction bytes that load `id`.
auto Encode(const Site& s, int id, uint8_t* out) noexcept -> size_t {
	if (s.form == Form::MovEdx) {
		out[0] = 0xba;
		std::memcpy(out + 1, &id, 4);
		return 5;
	}
	out[0] = 0x8d;
	out[1] = s.modrm;
	out[2] = static_cast<uint8_t>(id);
	return 3;
}

}  // namespace

auto CheckItemTypeIds(const D2RL::PluginContext* context, char* report, size_t reportSize) noexcept -> bool {
	int match = 0, idDiffers = 0, bytesDiffer = 0;
	char first[128] = "";
	for (const auto& s : Sites) {
		uint8_t want[5];
		const size_t n = Encode(s, s.d2rId, want);
		const bool bytes = std::memcmp(reinterpret_cast<const void*>(context->exeBase + s.rva), want, n) == 0;
		const int mod = ModId(s.code);
		if (bytes && mod == s.d2rId) {
			++match;
			continue;
		}
		if (!bytes) {
			++bytesDiffer;
			plog::Warn("item type check: site 0x%llx (%s) does not load D2R's id %d: another image, or patched",
			           static_cast<unsigned long long>(s.rva), s.code, s.d2rId);
		} else {
			++idDiffers;
			plog::Warn("item type check: %s is id %d in the mod's itemtypes.txt, D2R tests it as %d (site 0x%llx)", s.code, mod, s.d2rId,
			           static_cast<unsigned long long>(s.rva));
		}
		if (first[0] == '\0') {
			std::snprintf(first, sizeof(first), "; first: %s mod %d / D2R %d at 0x%llx", s.code, mod, s.d2rId, static_cast<unsigned long long>(s.rva));
		}
	}
	constexpr int Total = static_cast<int>(sizeof(Sites) / sizeof(Sites[0]));
	if (match == Total) {
		std::snprintf(report, reportSize, "ok: %d of %d sites, the mod's ids are D2R's", match, Total);
		return true;
	}
	std::snprintf(report, reportSize, "MISMATCH: %d of %d ok, %d ids differ, %d sites changed%s", match, Total, idDiffers, bytesDiffer, first);
	return false;
}
