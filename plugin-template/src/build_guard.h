// Build guard: every RVA, entry-byte check and record offset in this plugin belongs to ONE game image. Check it before
// anything is installed, and stay off on a mismatch: a D2R or D2RLoader update must never make the plugin patch
// random code.
//
// The test is the image itself, not a version label: the PE header TimeDateStamp of the running D2R image
// (context->exeBase). Steam and Battle.net ship the same 3.3 program under different build numbers (94995 / 93847,
// both stamp 0x6ab3782c under D2RLoader 1.3.1), so a build-number check would wrongly refuse one of them. The entry
// bytes every InstallInlineHook checks are the second line of defence.
//
// On a mismatch: install no part, log why, say it in the status command (and, if you like, on screen with D2RLoader's
// overlay service). After a game update: re-dump, re-find every address (knowledge/engine-re.md), then change
// ExpectedImage.
#pragma once

#define NOMINMAX
#include <windows.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace build_guard {

constexpr uint32_t ExpectedImage = 0x6ab3782cU;  // D2R 3.3.0 under D2RLoader 1.3.1 (Battle.net 93847 / Steam 94995)

// TimeDateStamp of the PE image at `exeBase` (0 when there is none).
inline auto ImageStamp(uintptr_t exeBase) noexcept -> uint32_t {
	if (exeBase == 0) {
		return 0;
	}
	const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(exeBase);
	if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
		return 0;
	}
	const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(exeBase + static_cast<uintptr_t>(dos->e_lfanew));
	return nt->Signature == IMAGE_NT_SIGNATURE ? nt->FileHeader.TimeDateStamp : 0;
}

constexpr auto Matches(uint32_t stamp) noexcept -> bool {
	return stamp == ExpectedImage;
}

// The status / log line: "image 0x6ab3782c (expects 0x6ab3782c, match)" or "... MISMATCH: engine parts off".
inline void Describe(char* out, size_t size, uint32_t stamp, const char* buildVersion) noexcept {
	std::snprintf(out, size, "D2R %s, image 0x%08x (expects 0x%08x, %s)", buildVersion != nullptr ? buildVersion : "?", stamp,
	              ExpectedImage, Matches(stamp) ? "match" : "MISMATCH: engine parts off, re-find the addresses for this image");
}

}  // namespace build_guard
