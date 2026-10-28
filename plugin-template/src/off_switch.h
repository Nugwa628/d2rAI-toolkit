// Diagnosis switch: "<plugin>-off.txt" next to the DLL (e.g. mods\<Mod>\d2rloader\plugins\mod-core-off.txt) lists
// the words of parts to leave out, separated by spaces, commas or lines, case-insensitive. Delete the file for normal
// play.
//
// Use it to bisect a crash or regression: leave half the parts out, restart the game, repeat. Build it in from day
// one, give every new part a word, and know the dependencies (a part whose calc codes other parts' formulas use can't
// be left out alone: the game may hang on load).
//
// Example file:   envguard soundguard
#pragma once

namespace off_switch {

// Reads the file named after this DLL (<dir>\<dll name>-off.txt, so a renamed plugin reads its own file).
void Read() noexcept;

// True when the file names this word (whole words only: "hook" does not match "hooks").
auto Off(const char* word) noexcept -> bool;

// The words read, lower case, separated by spaces ("" when there is no file).
auto Words() noexcept -> const char*;

}  // namespace off_switch
