// The mod's itemtypes.txt ids of the item types D2R.exe tests by number (item_type_check.cpp).
//
// GENERATED from the mod's built itemtypes.txt by plugin-template/tools/item_type_ids.py:
//   python plugin-template/tools/item_type_ids.py <project>/out/data/global/excel/itemtypes.txt plugin/src/item_type_ids.h
// The values below are vanilla D2R 3.3's (a mod that leaves itemtypes alone keeps them). Regenerate after every change
// to itemtypes.txt; the build should fail when an id differs from D2R's (the generator exits 1).
#pragma once

namespace item_type_ids {

struct Entry {
	const char* code;
	int id;
};

inline constexpr Entry Mod[] = {
	{"jewl", 58}, {"h2h", 68}, {"pelt", 73}, {"rune", 75}, {"hpot", 77}, {"apot", 81},
	{"wpot", 82}, {"scha", 83}, {"mcha", 84}, {"gem4", 96},
};

}  // namespace item_type_ids
