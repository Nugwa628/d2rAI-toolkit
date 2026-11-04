# Strings

## Files

`data/local/lng/strings/*.json` (HD) and `data/local/lng/strings-legacy/*.json` (legacy mode text; a subset of the
same files). 93847 files in `strings/`: `bnet`, `chinese-overlay`, `commands`, `item-gems`, `item-modifiers`,
`item-nameaffixes`, `item-names`, `item-runes`, `keybinds`, `levels`, `mercenaries`, `monsters`, `npcs`,
`objects`, `presence-states`, `quests`, `shrines`, `skills`, `ui-controller`, `ui`, `vo` (+ `metadata/`). Also
per-language folders (`eng`, `deu`, ...) and `subtitles/`.

Each file is a JSON array (UTF-8 **with BOM**, CRLF line ends in D2R's files):

```json
{ "id": 1296, "Key": "Eskillname0", "enUS": "...", "zhTW": "...", "deDE": "...", "esES": "...", "frFR": "...",
  "itIT": "...", "koKR": "...", "plPL": "...", "esMX": "...", "jaJP": "...", "ptBR": "...", "ruRU": "...", "zhCN": "..." }
```

13 languages: enUS zhTW deDE esES frFR itIT koKR plPL esMX jaJP ptBR ruRU zhCN. Tables reference strings by `Key`
(e.g. items `namestr`, skilldesc `str name`, levels `LevelName`); layouts by `@Key`. Numeric keys (`"115"`) are
legal keys and stay as they are.

## Native loading (default): rules

1. **A mod's string file replaces the game's whole file of that name.** Ship complete copies of every file you
   touch (D2R's records + your changes + your new records), in both `strings/` and `strings-legacy/` where D2R has
   that file. A missing record shows its key in game.
2. **IDs**: `id` values are shared across all files and must be unique. **D2R matches string IDs by their low 16
   bits.** Use `data/local/lng/next_string_id.txt` ("NEXT VALID ID", **28106** in 93847) and count up, **skipping
   every value whose low 16 bits are already used** by any D2R string, and stay **below 65,536**. D2R's highest ID
   is 251780 (a single entry in `commands.json`; its low 16 bits are 55172, so skip 55172), its highest below
   65,536 is 28105 (12,245 strings in `strings/`).
   - Do **not** number new strings above 251780: a mod that does gets thousands of IDs that collide with D2R
     strings in their low 16 bits (an NPC's first greeting shows a skill name: an ID of 4 x 65536 + 45 answers
     for string 45).
   - That gives roughly 37,000 free IDs (28106..65535 minus used 16-bit values). Table string references use a
     two-byte pool (65,535) shared by all strings.
   - Use the same ID for a key in both `strings/` and `strings-legacy/`.
3. **printf placeholders are a contract.** D2R formats some strings itself with `sprintf`-style calls, e.g.
   `sysmsg7-9` (the "was slain by" fragments joined to unit names), tooltip lines ("Durability: %d of %d",
   "Required Strength: %d"). When overriding a D2R key keep **the same placeholder list in the same order**
   (`%d`, `%s`, `%+d`, `%%` literal). A changed `%s` count reads a missing argument and **crashes** (the death
   message crash in an overhaul). Check with a regex over `%[flags][width][.prec][len]conv`; keep D2R's text
   where they differ.
4. Keys that the engine fills in (`%s` the skill name in a synergy header, etc.) need their placeholders even if
   your text doesn't want them.
5. Overriding a D2R key: change every occurrence (some keys appear in several files) in every language you
   support. Filling all 13 languages with your text stops non-English clients showing vanilla text that
   contradicts the mod.
6. Duplicate keys: D2R itself ships 121 duplicate IDs; don't add more.

## D2RLoader namespaces (alternative, not used by the port)

From the PluginSDK's `LOCALIZATION.md`:

- Opt in with a top-level `"namespace": "<name>"` in `<mod>/d2rloader/metadata.json` (next to `schemaVersion`).
  Without it, mod strings use native loading (rules above).
- Namespaced string files: any filename under `data/local/lng/strings/`, records `{ "Key": ..., "enUS": ... }`
  without `id` (ignored). No fallback to English for missing languages.
- Table cells: unqualified keys always mean `d2r:<key>`; your own strings must be written `namespace:Key` in table
  cells, and the full `namespace:key` must fit fixed-size fields (affix names). Layouts: `@Key` (own) or
  `@d2r:strCancel`.
- Override any existing text with `{ "Key": "d2r:strCancel", "enUS": "..." }`; only the supplied languages change.
- The loader assigns two-byte references when tables load; the shared pool is finite (an error when full).
- Rebuild BINs after changing the namespace; packed MPQs need a `(listfile)` for arbitrary string filenames.
- Plugins own the namespace equal to their `PluginInfo.id`.

Namespaces avoid ID management and the complete-file rule; native loading keeps plain keys in tables (simplest
when converting existing data).

### Namespaces in practice (observed with D2RLoader 1.3.1, D2R 3.3 build 93847)

- **A namespaced string has no stable numeric ID.** Only keys that a table cell references get a two-byte number,
  handed out when the tables load from the lowest numbers D2R's strings leave free, in load order. The same data
  gives the same numbers each launch, but any change to which keys the tables reference moves them. Never store or
  hard-code such a number; a plugin asks for text by full key (`namespace:Key`) through the PluginSDK localization
  service at the moment of use (it follows language changes). The service's by-ID lookup answers only D2R's own
  IDs; D2R's own numeric lookup (`0x3e2b730`, build 93847) does resolve the temporary numbers.
- Keys that no table references take no number at all, so the 65,535 pool is rarely the constraint (D2R uses about
  12,100 distinct IDs). A missing key in a table cell, or a full pool, **stops startup** with a loader error naming
  the key; it never wraps.
- Accepted in every localization column tried: item `namestr`, monstats `NameStr` / `DescStr`, skilldesc
  `str name/short/long/alt`, itemstatcost `descstrpos/descstrneg/descstr2/dgrpstrpos`, levels `LevelName` /
  `LevelWarp` / `LevelEntry`, uniqueitems / setitems `index`, sets / runes / superuniques / lowqualityitems names,
  magic and rare affix names, misc `spelldescstr`.
- **No English fallback**: a record without the game's language shows a "missing string" placeholder. Fill all 13.
- With the namespace on, complete copies of D2R's files still override D2R's keys, but records in them whose key D2R
  doesn't have are filed under the mod's namespace and their `id` is ignored (their old numeric IDs stop working).
- Fixed-size name fields (dataguide `dataLength`, e.g. 31 for uniqueitems / setitems `index`, 39 for levels
  `LevelWarp`) must hold the whole `namespace:Key`. A longer key gave no loader error in one test, so it is either
  stored elsewhere or cut silently: unsettled, keep keys short (an alias key is the safe route).
- Namespaced files in `strings/` also resolve in legacy graphics mode.
- UI functions that take text (for example the NPC menu line adder) can be fed the looked-up text directly; ones
  that take a string ID need a native ID.