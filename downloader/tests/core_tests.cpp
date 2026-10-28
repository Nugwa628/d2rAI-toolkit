// Unit tests of the downloader's portable core (no Windows headers): manifest parsing and the install plan, path
// safety, the mod folder and its marker, the D2RLoader version check, D2R detection parsers and the build check, the
// 7z decoder, TOML edits.
// Built and run by CI on Linux (g++) and locally with MSVC: cmake -S downloader/tests -B build/downloader-tests.
#include "../src/core/detect.h"
#include "../src/core/json.h"
#include "../src/core/manifest.h"
#include "../src/core/paths.h"
#include "../src/core/sevenzip.h"
#include "../src/core/sha256.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <map>
#include <string>
#include <vector>

namespace {
int failures = 0, checks = 0;
void Check(bool ok, const char* what) {
	++checks;
	if (!ok) {
		++failures;
		std::printf("FAIL: %s\n", what);
	}
}

const std::string H1(64, 'a'), H2(64, 'b'), H3(64, 'c'), H4(64, 'd');

std::string ManifestText(const std::string& files, const std::string& extra = "") {
	return R"({"schemaVersion": 2, "name": "TestMod", "version": "2.10.0", "pluginVersion": "2.10.0", "complete": true,
	  "d2r": {"build": "93847", "version": "3.3.0", "imageTimestamp": "0x6ab3782c"},
	  "d2rloader": {"version": "1.3.1-beta", "url": "https://d2rloader.net/download/D2RLoader-1.3.1-beta.zip", "size": 47652455,
	                "sha256": "9286C6B5BFF7F1043658411DBEA305FAA9698B6A455E6441668FC6D02C22AAD7", "signerThumbprint": "8001082E968FADAA58BF2F73EFA1810A11D0C28F"},
	  "launch": {"default_mod": "TestMod", "launch_arguments": "-txt"}, "modFolder": "TestMod", "saveFolder": "TestMod/",
	  "archive": {"format": "7z", "name": "TestMod-2.10.0.7z", "size": 100, "sha256": ")" + H1 + R"("}, "someFutureField": {"x": [1]},
	  "objectsBase": "objects/", "emptyDirs": ["d2rloader/patches"],
	  "files": [)" + files + "]" + extra + "}";
}
std::string Without(std::string text, const std::string& part) {
	size_t at = text.find(part);
	return at == std::string::npos ? text : text.erase(at, part.size());
}
std::string F(const std::string& path, const std::string& sha, int size = 10) {
	return R"({"path": ")" + path + R"(", "size": )" + std::to_string(size) + R"(, "sha256": ")" + sha + R"("})";
}

void TestSha256() {
	Check(dl::Sha256Hex("", 0) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "sha256 of nothing");
	Check(dl::Sha256Hex("abc", 3) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256 abc");
	std::string m = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
	Check(dl::Sha256Hex(m.data(), m.size()) == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1", "sha256 two blocks");
	std::string a(1000000, 'a');
	dl::Sha256 s;
	for (size_t i = 0; i < a.size(); i += 333)  // odd chunk sizes across block borders
		s.Update(a.data() + i, std::min<size_t>(333, a.size() - i));
	Check(s.HexDigest() == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", "sha256 million a, chunked");
}

void TestJson() {
	dl::Json j;
	std::string err;
	Check(dl::Json::Parse(R"({"a": [1, 2.5, "x\u00e9\n"], "b": {"c": true, "d": null}, "e": -3})", j, err), "json parses");
	Check(j["a"].size() == 3 && j["a"][1].Num() == 2.5 && j["a"][2].Str() == "x\xc3\xa9\n", "json array and \\u escape");
	Check(j["b"]["c"].Bool() && j["b"]["d"].IsNull() && j["e"].Int() == -3, "json nested");
	Check(j["missing"]["deeper"].Str("fb") == "fb", "missing keys give the fallback");
	Check(!dl::Json::Parse("{\"a\": 1,}", j, err), "trailing comma refused");
	Check(!dl::Json::Parse("{\"a\": 1} x", j, err), "trailing data refused");
	dl::Json o = dl::Json::Object();
	o.Set("path", "a\"b\\c").Set("n", uint64_t(5000000000ull));
	dl::Json back;
	Check(dl::Json::Parse(o.Dump(), back, err) && back["path"].Str() == "a\"b\\c" && back["n"].Int() == 5000000000ll,
	      "json round trip with escapes and a large size");
}

void TestSafePath() {
	Check(dl::SafeRelativePath(DL_MOD_FOLDER ".mpq/data/global/excel/skills.txt"), "normal path");
	Check(!dl::SafeRelativePath("../x"), "parent refused");
	Check(!dl::SafeRelativePath("a/../../x"), "inner parent refused");
	Check(!dl::SafeRelativePath("/abs"), "absolute refused");
	Check(!dl::SafeRelativePath("C:/x"), "drive refused");
	Check(!dl::SafeRelativePath("a\\b"), "backslash refused");
	Check(!dl::SafeRelativePath("a//b"), "empty segment refused");
	Check(!dl::SafeRelativePath("a/./b"), "dot segment refused");
	Check(!dl::SafeRelativePath("a/nul.txt"), "device name refused");
	Check(!dl::SafeRelativePath("a/b "), "trailing space refused");
	Check(!dl::SafeRelativePath("a/b?"), "wildcard refused");
	Check(!dl::SafeRelativePath(""), "empty refused");
}

void TestManifest() {
	dl::Manifest m;
	std::string err;
	Check(dl::ParseManifest(ManifestText(F("a.txt", H1) + "," + F("d2rloader/config/d2rloader.toml", H2),
	                                      R"(, "preserve": ["d2rloader/config/d2rloader.toml"], "fileCount": 2, "totalSize": 20)"), m, err),
	      "manifest parses (release.md schemaVersion 2, unknown fields ignored)");
	Check(m.files.size() == 2 && m.d2rBuild == 93847 && m.modFolder == "TestMod" && m.preserve.count("d2rloader/config/d2rloader.toml") &&
	          m.complete && m.imageTimestamp == 0x6ab3782cu && m.launchArguments == "-txt" && m.saveFolder == "TestMod/" &&
	          m.archive.name == "TestMod-2.10.0.7z" && m.archive.size == 100 && m.d2rloader.size == 47652455 &&
	          m.emptyDirs.size() == 1 && m.emptyDirs[0] == "d2rloader/patches" && m.objectsBase == "objects/" &&
	          m.d2rloader.sha256 == "9286c6b5bff7f1043658411dbea305faa9698b6a455e6441668fc6d02c22aad7",
	      "manifest fields");
	Check(!dl::ParseManifest(ManifestText(F("a.txt", H1), R"(, "fileCount": 3)"), m, err), "fileCount mismatch refused");
	Check(!dl::ParseManifest(ManifestText(F("a.txt", H1), R"(, "totalSize": 11)"), m, err), "totalSize mismatch refused");
	Check(!dl::ParseManifest(ManifestText(F("../evil.dll", H1)), m, err), "manifest with an unsafe path refused");
	Check(!dl::ParseManifest(ManifestText(F("a.txt", "xyz")), m, err), "manifest with a bad sha refused");
	Check(!dl::ParseManifest(ManifestText(F("a.txt", H1) + "," + F("A.TXT", H2)), m, err), "case-insensitive duplicate refused");
	Check(!dl::ParseManifest(ManifestText(""), m, err), "empty file list refused");
	// schema 1 (the zip) is too old, 3 too new; both refused, with what the caller needs for its message
	std::string v1 = ManifestText(F("a.txt", H1));
	v1.replace(v1.find(R"("schemaVersion": 2)"), 18, R"("schemaVersion": 1)");
	Check(!dl::ParseManifest(v1, m, err) && m.schemaVersion == 1 && m.version == "2.10.0" && m.files.empty(),
	      "schemaVersion 1 (zip releases) refused");
	std::string v3 = ManifestText(F("a.txt", H1), R"(, "launcher": {"version": "2.9.0", "path": "launcher/2.9.0/Downloader.exe", "size": 5, "sha256": ")" + H3 + R"("})");
	v3.replace(v3.find(R"("schemaVersion": 2)"), 18, R"("schemaVersion": 3)");
	Check(!dl::ParseManifest(v3, m, err) && m.schemaVersion == 3 && m.launcher.version == "2.9.0" && m.launcher.sha256 == H3,
	      "schemaVersion 3 refused, its launcher still offered");
	Check(!dl::ParseManifest(R"({"schemaVersion": 3})", m, err) && m.schemaVersion == 3 && m.launcher.path.empty(), "bare future schema refused");
	std::string noObjects = ManifestText(F("a.txt", H1));
	noObjects.replace(noObjects.find(R"("objectsBase": "objects/", )"), 27, "");
	Check(!dl::ParseManifest(noObjects, m, err), "manifest without objectsBase refused (small updates and Repair use the objects)");
	std::string dirs = ManifestText(F("a.txt", H1));
	dirs.replace(dirs.find("d2rloader/patches"), 17, "../x");
	Check(!dl::ParseManifest(dirs, m, err) && err.find("emptyDirs") != std::string::npos, "unsafe emptyDirs refused");
	Check(!dl::ParseManifest(R"({"schema": 1})", m, err), "old draft format refused");
	dl::Manifest back;
	dl::ParseManifest(ManifestText(F("a.txt", H1), R"(, "preserve": ["a.txt"])"), m, err);
	Check(dl::ParseManifest(dl::ManifestToJson(m).Dump(), back, err) && back.files.size() == 1 && back.files[0].sha256 == H1 &&
	          back.imageTimestamp == m.imageTimestamp && back.preserve.count("a.txt") && back.d2rBuild == 93847 && back.complete &&
	          back.emptyDirs == m.emptyDirs && back.archive.name == m.archive.name && back.archive.sha256 == H1,
	      "manifest round trip (state file)");
}

void TestHosting() {
	dl::Manifest m;
	std::string err;
	std::string pub = R"(, "launcher": {"version": "1.2.0", "path": "launcher/1.2.0/Downloader.exe", "size": 5, "sha256": ")" + H3 + R"("})";
	std::string text = ManifestText(F("a.txt", H1) + "," + F("b/copy.txt", H1) + "," + F("c.txt", H2), pub);
	text.replace(text.find(R"("name": "TestMod-2)"), 0, R"("path": "releases/2.10.0/TestMod-2.10.0.7z", )");
	Check(dl::ParseManifest(text, m, err), "published manifest parses");
	Check(m.objectsBase == "objects/" && m.archive.path == "releases/2.10.0/TestMod-2.10.0.7z" && m.launcher.version == "1.2.0" &&
	          m.launcher.sha256 == H3 && m.launcher.size == 5,
	      "objectsBase, archive.path, launcher");
	dl::Manifest back;
	Check(dl::ParseManifest(dl::ManifestToJson(m).Dump(), back, err) && back.objectsBase == "objects/" && back.archive.path == m.archive.path &&
	          back.launcher.path == m.launcher.path,
	      "published fields round trip");
	Check(!dl::ParseManifest(Without(ManifestText(F("a.txt", H1), R"(, "objectsBase": "../x/")"), R"("objectsBase": "objects/", )"), m, err), "unsafe objectsBase refused");
	Check(!dl::ParseManifest(Without(ManifestText(F("a.txt", H1), R"(, "objectsBase": "objects")"), R"("objectsBase": "objects/", )"), m, err), "objectsBase without '/' refused");
	Check(!dl::ParseManifest(Without(ManifestText(F("a.txt", H1), R"(, "objectsBase": "https://evil/")"), R"("objectsBase": "objects/", )"), m, err), "absolute objectsBase refused");
	Check(dl::ObjectPath("objects/", H2) == "objects/bb/" + H2, "object path");
	auto groups = dl::GroupObjects({{"a.txt", 10, H1}, {"b/copy.txt", 10, H1}, {"c.txt", 7, H2}});
	Check(groups.size() == 2 && groups[0].sha256 == H1 && groups[0].paths.size() == 2 && groups[0].paths[1] == "b/copy.txt" &&
	          groups[1].size == 7 && groups[1].paths.size() == 1,
	      "same content is one download");

	dl::Channel c;
	Check(dl::ParseChannel(R"({"schemaVersion": 1, "version": "2.10.0", "manifest": "releases/2.10.0/release-manifest.json",
	    "manifestSha256": ")" + std::string(64, 'E') + R"(", "publishedAt": "2030-01-30T12:00:00Z", "later": 1})", c, err) &&
	          c.version == "2.10.0" && c.manifest == "releases/2.10.0/release-manifest.json" && c.manifestSha256 == std::string(64, 'e'),
	      "channel parses");
	Check(!dl::ParseChannel(R"({"schemaVersion": 2, "version": "1", "manifest": "m.json", "manifestSha256": ")" + H1 + R"("})", c, err),
	      "unknown channel schemaVersion refused");
	Check(!dl::ParseChannel(R"({"schemaVersion": 1, "version": "1", "manifest": "../m.json", "manifestSha256": ")" + H1 + R"("})", c, err),
	      "unsafe channel manifest path refused");
	Check(!dl::ParseChannel(R"({"schemaVersion": 1, "version": "1", "manifest": "m.json"})", c, err), "channel without sha refused");
	Check(!dl::ParseChannel("<html>sign in</html>", c, err), "a login page is not a channel");
	const std::string chUrl = "https://files.example.com/mymod/channels/stable.json";
	Check(dl::ChannelReadError("stable", chUrl, 404, "") == "Nothing is published on the stable channel yet.",
	      "channel 404: nothing published yet");
	Check(dl::ChannelReadError("test", "file:///D:/site/channels/test.json", 404, "cannot open") ==
	          "Nothing is published on the test channel yet.",
	      "missing channel file on a local site: nothing published yet");
	Check(dl::ChannelReadError("stable", chUrl, 503, "") == "The download site returned HTTP 503 for " + chUrl, "other status: the site answered it");
	Check(dl::ChannelReadError("stable", chUrl, 403, "") == "The download site returned HTTP 403 for " + chUrl, "403 is not 'not published'");
	std::string net = dl::ChannelReadError("stable", chUrl, 0, "download x: The server name or address could not be resolved (12007)");
	Check(net.rfind("Download site unreachable (" + chUrl + "): ", 0) == 0 && net.find("(12007)") != std::string::npos,
	      "no answer: cannot reach, with the WinHTTP error");
	Check(dl::ChannelReadError("stable", chUrl, 0, "") == "Download site unreachable (" + chUrl + ")", "no answer without error text");

	Check(dl::SiteRoot("https://files.example.com/mymod") == "https://files.example.com/mymod/", "site root gets a '/'");
	Check(dl::SiteRoot("http://127.0.0.1:8765/") == "http://127.0.0.1:8765/", "site root kept");
	const std::string bs(1, char(92));
	Check(dl::SiteRoot("D:" + bs + "test site" + bs) == "file:///D:/test%20site/", "local folder becomes a file URL");
	Check(dl::SiteRoot(bs + bs + "srv" + bs + "mymod") == "file://srv/mymod/", "UNC share becomes a file URL");
	Check(dl::ResolveUrl(dl::SiteRoot("http://h/mymod"), "objects/aa/x") == "http://h/mymod/objects/aa/x", "object URL under the root");
}

void TestPlan() {
	std::string err;
	dl::Manifest next;
	dl::ParseManifest(ManifestText(F("same.txt", H1) + "," + F("changed.txt", H2, 20) + "," + F("new.txt", H3, 30) + "," +
	                                F("d2rloader/config/d2rloader.toml", H2) + "," + F("missing.txt", H1, 5),
	                                R"(, "preserve": ["d2rloader/config/d2rloader.toml"])"),
	                   next, err);
	std::map<std::string, std::string> disk = {{"same.txt", H1}, {"changed.txt", H1}, {"gone.txt", H1},
	                                           {"d2rloader/config/d2rloader.toml", H4}, {"d2rloader/logs/x.log", H1},
	                                           {"d2rloader/crashes/c.dmp", H1}, {"stray/own.txt", H1}};
	auto cur = [&](const std::string& p) { auto it = disk.find(p); return it == disk.end() ? std::string() : it->second; };
	auto local = [&] { std::vector<std::string> v; for (const auto& [k, _] : disk) v.push_back(k); return v; };
	dl::InstallPlan plan = dl::PlanInstall(next, local(), cur);
	auto has = [](const std::vector<dl::ManifestFile>& v, const char* p) {
		for (const auto& f : v) if (f.path == p) return true;
		return false;
	};
	auto hasS = [](const std::vector<std::string>& v, const char* p) {
		for (const auto& f : v) if (f == p) return true;
		return false;
	};
	Check(!has(plan.fetch, "same.txt"), "unchanged file is not fetched");
	Check(has(plan.fetch, "changed.txt") && has(plan.fetch, "new.txt") && has(plan.fetch, "missing.txt"), "changed, new, missing fetched");
	Check(hasS(plan.preserved, "d2rloader/config/d2rloader.toml") && !has(plan.fetch, "d2rloader/config/d2rloader.toml"),
	      "existing preserve file never overwritten");
	Check(hasS(plan.remove, "gone.txt") && hasS(plan.remove, "stray/own.txt") && plan.remove.size() == 2,
	      "unlisted files removed, D2RLoader logs / crashes kept");
	Check(plan.fetchBytes == 20 + 30 + 5, "fetch byte count");
	disk.clear();
	plan = dl::PlanInstall(next, local(), cur);
	Check(plan.fetch.size() == next.files.size() && plan.remove.empty(), "fresh install fetches all, preserve file included");
	for (const auto& f : next.files) disk[f.path] = f.sha256;
	plan = dl::PlanInstall(next, local(), cur);
	Check(plan.fetch.empty() && plan.remove.empty() && plan.preserved.empty(), "verified install: nothing to do");
}

void TestVersions() {
	Check(dl::CompareVersions("1.2.10", "1.2.9") > 0, "1.2.10 > 1.2.9");
	Check(dl::CompareVersions("v2.10.0", "2.10.0") == 0, "leading v");
	Check(dl::CompareVersions("1.3.1", "1.3.1-beta") > 0, "release > beta");
	Check(dl::CompareVersions("1.3", "1.3.0") == 0, "missing parts are 0");
	Check(dl::ResolveUrl("https://github.com/o/r/releases/latest/download/manifest.json", DL_MOD_FOLDER "-2.10.0.zip") ==
	          "https://github.com/o/r/releases/latest/download/" DL_MOD_FOLDER "-2.10.0.zip", "relative url");
	Check(dl::ResolveUrl("file:///C:/feed/manifest.json", "x.zip") == "file:///C:/feed/x.zip", "relative file url");
	Check(dl::ResolveUrl("http://127.0.0.1:8000/a/m.json", "/b/x.zip") == "http://127.0.0.1:8000/b/x.zip", "host-relative url");
	Check(dl::ResolveUrl("https://a/m.json", "https://b/x.zip") == "https://b/x.zip", "absolute url");
}

void TestDetect() {
	std::string info =
		"Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\r\n"
		"eu|0|aaaa|3.3.90000|osi\r\n"
		"us|1|623f|3.3.93847|osi\r\n";
	Check(dl::BuildInfoVersion(info) == "3.3.93847", ".build.info active row");
	Check(dl::BuildInfoVersion("Version!STRING:0\n") == "", ".build.info without rows");
	Check(dl::BuildInfoVersion("garbage") == "", "no Version column");
	std::string real =
		"Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16|IM Size!DEC:4|CDN Path!STRING:0|CDN Hosts!STRING:0|"
		"CDN Servers!STRING:0|Tags!STRING:0|Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|KeyRing!HEX:16|Product!STRING:0\n"
		"us|1|00112233445566778899aabbccddeeff|ffeeddccbbaa99887766554433221100|||tpr/osi|cdn.example.com|http://cdn.example.com/?maxhosts=5|Windows US|||3.3.93847||\n";
	Check(dl::BuildInfoVersion(real) == "3.3.93847", ".build.info with the usual columns");
	Check(dl::BuildNumber("3.3.93847") == 93847 && dl::BuildNumber("1.0.0.93847") == 93847 && dl::BuildNumber("") == 0, "build numbers");

	// The requirement is the game version (major.minor), never the build number (D2RLoader supports a game version).
	Check(dl::GameVersion("3.3.93847") == "3.3" && dl::GameVersion("3.3.0") == "3.3" && dl::GameVersion("1.0.0.93847") == "1.0" &&
	          dl::GameVersion("").empty() && dl::GameVersion("93847").empty(), "game version is major.minor");
	auto ok = dl::CheckBuild("3.3.93847", "3.3.93847");
	Check(ok.verdict == dl::BuildVerdict::Ok && ok.version == "3.3" && ok.build == 93847, "3.3.93847 is ok");
	auto steam = dl::CheckBuild("", "3.3.94000");  // Steam: no .build.info, another 3.3 build
	Check(steam.verdict == dl::BuildVerdict::Ok && steam.build == 94000, "any 3.3 build is ok (another store, 94000)");
	Check(dl::CheckBuild("3.3.99999", "").verdict == dl::BuildVerdict::Ok, "a later 3.3 hotfix build is ok");
	auto newer = dl::CheckBuild("3.4.95000", "3.3.93847");
	Check(newer.verdict == dl::BuildVerdict::Newer && newer.message.find("A new " DL_MOD_NAME " release is needed") != std::string::npos,
	      "3.4: waiting message (.build.info decides)");
	Check(dl::CheckBuild("3.2.91000", "").verdict == dl::BuildVerdict::Older, "older version");
	Check(dl::CheckBuild("3.10.1", "").verdict == dl::BuildVerdict::Newer, "3.10 is newer than 3.3");
	Check(dl::CheckBuild("", "").verdict == dl::BuildVerdict::Unknown, "unknown version");
	Check(dl::CheckBuild("", "3.3.93847").verdict == dl::BuildVerdict::Ok, "exe version alone");

	std::string vdf = R"("libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"apps" { "228980" "340606523" }
	}
	"1"
	{
		"path"		"E:\\SteamLibrary"   // a second library
	}
})";
	auto libs = dl::SteamLibraryPaths(vdf);
	Check(libs.size() == 2 && libs[0] == "C:\\Program Files (x86)\\Steam" && libs[1] == "E:\\SteamLibrary", "steam library paths");
	std::string acf = R"("AppState"
{
	"appid"		"2536520"
	"name"		"Diablo II: Resurrected"
	"installdir"		"Diablo II Resurrected"
	"UserConfig" { "name" "other" }
})";
	Check(dl::AcfValue(acf, "installdir") == "Diablo II Resurrected" && dl::AcfValue(acf, "name") == "Diablo II: Resurrected",
	      "acf top-level values (not the nested name)");
	Check(dl::AcfIsD2R(acf), "acf names D2R");
	Check(!dl::AcfIsD2R(R"("AppState" { "name" "Diablo IV" "installdir" "Diablo IV" })"), "Diablo IV is not D2R");
}

// docs/downloader.md "Where it writes": only <D2R>\mods\<modFolder>, proven ours by the marker.
void TestPaths() {
	const std::string bs(1, char(92));
	const std::string d2r = "C:" + bs + "Program Files (x86)" + bs + "Diablo II Resurrected";
	Check(dl::ModTargetDir(d2r, DL_MOD_FOLDER) == d2r + bs + "mods" + bs + DL_MOD_FOLDER, "mod folder <D2R>\\mods\\" DL_MOD_FOLDER);
	Check(dl::ModTargetDir(d2r + bs, DL_MOD_FOLDER) == d2r + bs + "mods" + bs + DL_MOD_FOLDER, "trailing separator");
	for (const char* bad : {"", ".", "..", "../x", "a/b", "a\\b", "C:", "CON", "x ", "x.", DL_MOD_FOLDER "/.."})
		Check(!dl::SafeModFolderName(bad) && dl::ModTargetDir(d2r, bad).empty(), bad);
	Check(dl::SafeModFolderName(DL_MOD_FOLDER) && dl::SafeModFolderName(DL_MOD_FOLDER "-lt"), "plain names allowed");
	const std::string mod = dl::ModTargetDir(d2r, DL_MOD_FOLDER);
	Check(dl::PathIsOrUnder(mod, mod) && dl::PathIsOrUnder(mod + bs + DL_MOD_FOLDER ".mpq" + bs + "x.txt", mod) &&
	          dl::PathIsOrUnder(mod + "/a/b", mod),
	      "files inside the mod folder");
	Check(!dl::PathIsOrUnder(mod + "-old", mod) && !dl::PathIsOrUnder(d2r + bs + "D2R.exe", mod) &&
	          !dl::PathIsOrUnder(d2r + bs + "mods" + bs + "OtherMod", mod) && !dl::PathIsOrUnder(d2r, mod),
	      "the rest of the D2R folder is outside");
	Check(!dl::PathIsOrUnder(mod + bs + ".." + bs + "OtherMod", mod) && !dl::PathIsOrUnder(mod + "/../../D2R.exe", mod),
	      "a '..' segment never counts as inside");
	Check(dl::PathIsOrUnder("c:/program files (x86)/diablo ii resurrected/MODS/mymod/x", mod), "case and separators");

	// the marker
	std::string m = dl::MakeMarker("id1", "2.10.0");
	Check(dl::CheckMarker(m, "id1") == dl::MarkerVerdict::Ours, "our marker, our id");
	Check(dl::CheckMarker(m, "") == dl::MarkerVerdict::Ours, "our marker, no id in the state yet");
	Check(dl::CheckMarker(m, "id2") == dl::MarkerVerdict::OtherId, "our marker, another state");
	Check(dl::CheckMarker("", "id1") == dl::MarkerVerdict::None, "no marker: not ours");
	Check(dl::CheckMarker("{nope", "id1") == dl::MarkerVerdict::Unreadable, "damaged marker");
	Check(dl::CheckMarker(R"({"made_by": "old downloader", "id": "id1"})", "id1") == dl::MarkerVerdict::Unreadable,
	      "another program's marker (an older tool's marker) is not ours");
	Check(dl::CheckMarker("{\"made_by\": \"" DL_PRODUCT_TITLE "\"}", "id1") == dl::MarkerVerdict::Unreadable, "marker without id");
	dl::Json j;
	std::string err;
	Check(dl::Json::Parse(m, j, err) && j["made_by"].Str() == dl::MarkerMadeBy && j["version"].Str() == "2.10.0", "marker content");

	// the plan never deletes the marker or the downloads in progress
	dl::Manifest next;
	dl::ParseManifest(ManifestText(F("a.txt", H1)), next, err);
	std::vector<std::string> local = {"a.txt", dl::InstallMarkerName, std::string(dl::DownloadFolderName) + "/" + H2 + ".part",
	                                  std::string(dl::DownloadFolderName) + "/7z/a.txt", "old.txt"};
	dl::InstallPlan plan = dl::PlanInstall(next, local, [](const std::string& p) { return p == "a.txt" ? H1 : std::string(); });
	Check(plan.remove.size() == 1 && plan.remove[0] == "old.txt" && plan.fetch.empty(), "marker and " DL_DOWNLOAD_FOLDER "/ kept, old.txt removed");

	// D2RLoader's version
	Check(dl::LoaderVersionMatches("1.3.1-beta", "1.3.1-beta") && dl::LoaderVersionMatches("1.3.1", "1.3.1-beta") &&
	          dl::LoaderVersionMatches("1.3.1.0", "1.3.1-beta"),
	      "D2RLoader 1.3.1 matches");
	Check(!dl::LoaderVersionMatches("1.3.0", "1.3.1-beta") && !dl::LoaderVersionMatches("1.4.0", "1.3.1-beta") &&
	          !dl::LoaderVersionMatches("", "1.3.1-beta") && !dl::LoaderVersionMatches("1.3.1.2", "1.3.1-beta") &&
	          !dl::LoaderVersionMatches("unknown version", "1.3.1-beta"),
	      "other D2RLoader versions do not");
}

// A ByteSource over memory, for the damaged-archive cases.
class MemSource : public dl::ByteSource {
public:
	explicit MemSource(std::string d) : d_(std::move(d)) {}
	bool Read(void* buf, size_t& size) override {
		size = std::min(size, d_.size() - size_t(pos_));
		std::memcpy(buf, d_.data() + pos_, size);
		pos_ += int64_t(size);
		return true;
	}
	bool Seek(int64_t& pos, int origin) override {
		int64_t base = origin == 0 ? 0 : origin == 1 ? pos_ : int64_t(d_.size());
		if (base + pos < 0 || base + pos > int64_t(d_.size()))
			return false;
		pos_ = pos = base + pos;
		return true;
	}

private:
	std::string d_;
	int64_t pos_ = 0;
};

std::string ReadAll(const std::string& path) {
	std::ifstream f(path, std::ios::binary);
	std::stringstream ss;
	ss << f.rdbuf();
	return ss.str();
}

// Extract everything; out: path -> sha256 (directories: "dir").
bool Unpack(dl::ByteSource& src, std::map<std::string, std::string>& out, std::string& err, dl::SevenZipStats* stats = nullptr,
            size_t skip = SIZE_MAX) {
	dl::SevenZipArchive a;
	if (!a.Open(src, err))
		return false;
	dl::Sha256 h;
	std::string cur;
	dl::SevenZipArchive::Sink s;
	s.begin = [&](size_t i) {
		const auto& e = a.entries()[i];
		if (e.dir) {
			out[e.path] = "dir";
			return false;
		}
		if (i == skip)
			return false;
		h = dl::Sha256();
		cur = e.path;
		return true;
	};
	s.data = [&](const uint8_t* p, size_t n) {
		h.Update(p, n);
		return true;
	};
	s.end = [&](size_t i) {
		if (i != skip && !a.entries()[i].dir)
			out[cur] = h.HexDigest();
		return true;
	};
	s.progress = [](uint64_t) { return true; };
	return a.Extract(s, err, stats);
}

// tests/data/sample.7z: made by tests/data/make_sample_7z.py with the release's 7-Zip switches (solid LZMA2, and a BCJ
// block for the .dll); sample.json lists its files with their sha256.
void TestSevenZip() {
	const std::string dir = DL_TEST_DATA;
	std::string archive = ReadAll(dir + "/sample.7z"), listing = ReadAll(dir + "/sample.json"), err;
	dl::Json want;
	Check(!archive.empty() && dl::Json::Parse(listing, want, err) && want["files"].size() >= 7, "sample.7z and sample.json present");
	MemSource src(archive);
	std::map<std::string, std::string> got;
	dl::SevenZipStats stats;
	Check(Unpack(src, got, err, &stats), ("sample.7z extracts: " + err).c_str());
	size_t same = 0;
	for (const auto& f : want["files"].items())
		same += got["SampleMod/" + f["path"].Str()] == f["sha256"].Str();
	Check(same == want["files"].size(), "every file's sha256 matches sample.json");
	Check(got["SampleMod/d2rloader/patches"] == "dir", "the empty folder is listed");
	Check(got.count("SampleMod/SampleMod.mpq/data/empty.txt") && got["SampleMod/SampleMod.mpq/data/empty.txt"] == dl::Sha256Hex("", 0), "empty file");
	Check(got.count("SampleMod/modinfo.json"), "UTF-8 content, plain name");
	Check(stats.streamedBlocks >= 1 && stats.memoryBlocks >= 1, "a streamed LZMA2 block and an in-memory BCJ block");
	Check(stats.peakBuffer > 0 && stats.peakBuffer <= (64u << 20), "decode buffers stay small for a small dictionary");

	{  // a skipped file's bytes are decoded but not handed out; the rest still checks
		MemSource s2(archive);
		std::map<std::string, std::string> part;
		dl::SevenZipArchive probe;
		MemSource s3(archive);
		probe.Open(s3, err);
		size_t big = 0;
		for (size_t i = 0; i < probe.entries().size(); ++i)
			if (probe.entries()[i].path.find("big.txt") != std::string::npos)
				big = i;
		Check(Unpack(s2, part, err, nullptr, big) && !part.count("SampleMod/SampleMod.mpq/data/global/excel/big.txt") &&
		          part["SampleMod/modinfo.json"] == got["SampleMod/modinfo.json"],
		      "skipping a file");
	}
	{  // one flipped byte in the packed data: refused (CRC or LZMA error)
		std::string bad = archive;
		bad[64] = char(bad[64] ^ 0x5a);
		MemSource s2(bad);
		std::map<std::string, std::string> x;
		Check(!Unpack(s2, x, err), "a damaged archive is refused");
	}
	{  // cut short: the header at the end is missing
		MemSource s2(archive.substr(0, archive.size() / 2));
		std::map<std::string, std::string> x;
		Check(!Unpack(s2, x, err), "a truncated archive is refused");
	}
	{
		MemSource s2("not a 7z archive at all");
		std::map<std::string, std::string> x;
		Check(!Unpack(s2, x, err), "junk is refused");
	}
}

// docs/downloader.md "Where it writes": the default folders never land in the official one's.
void TestGameProcessName() {
	Check(dl::IsGameProcessName("D2R.exe"), "D2R.exe is the game");
	Check(dl::IsGameProcessName(R"(C:\Program Files (x86)\Diablo II Resurrected\d2r.EXE)"), "full path, any case");
	Check(dl::IsGameProcessName("D2RLoader.exe"), "D2RLoader.exe is the game");
	Check(dl::IsGameProcessName("D2RLoader-s2.exe"), "a slot's renamed loader is the game");
	Check(!dl::IsGameProcessName(DL_EXE_NAME ".exe"), "the downloader is not the game");
	Check(!dl::IsGameProcessName(DL_MOD_FOLDER "-Old.exe"), "an older downloader is not the game");
	Check(!dl::IsGameProcessName("Diablo II Resurrected Launcher.exe"), "Battle.net's launcher is not the game");
	Check(!dl::IsGameProcessName("BlizzardError.exe"), "the crash reporter is not the game");
	Check(!dl::IsGameProcessName("D2R.exe.bak"), "not an exe name");
	Check(!dl::IsGameProcessName(""), "empty");
}

void TestCoexist() {
	const std::string bs(1, char(92));
	const std::string local = "C:" + bs + "Users" + bs + "p" + bs + "AppData" + bs + "Local";
	const std::string roaming = "C:" + bs + "Users" + bs + "p" + bs + "AppData" + bs + "Roaming";
	std::string data = dl::DefaultDataDir(local);
	Check(data == local + bs + DL_STATE_FOLDER, "data folder in the state folder");
	const std::string mod = dl::ModTargetDir("C:" + bs + "Program Files (x86)" + bs + "Diablo II Resurrected", DL_MOD_FOLDER);
	for (const std::string& p : {data, mod, data + bs + "download"})
		Check(!dl::ProtectedPath(p, local, roaming), "the downloader's own folders are never protected");
	Check(dl::ProtectedPath("C:" + bs + "Program Files (x86)" + bs + "Diablo II", local, roaming) &&
	          dl::ProtectedPath("E:" + bs + "Games" + bs + "diablo ii" + bs + DL_MOD_FOLDER, local, roaming),
	      "a classic Diablo II folder is refused");
	Check(!dl::ProtectedPath("C:" + bs + "Program Files (x86)" + bs + "Diablo II Resurrected", local, roaming) &&
	          !dl::ProtectedPath(local + bs + "SomethingElse", local, roaming),
	      "similar names are not refused");
	// the folders named in DL_PROTECTED_APPDATA_FOLDERS (empty in the default config)
	static const char* const named[] = {DL_PROTECTED_APPDATA_FOLDERS nullptr};
	for (const char* const* f = named; *f; ++f)
		Check(dl::ProtectedPath(local + bs + *f, local, roaming) && dl::ProtectedPath(roaming + "/" + *f + "/save", local, roaming) &&
		          !dl::ProtectedPath(local + bs + *f + "2", local, roaming),
		      *f);
}

void TestToml() {
	std::string t = "# head\n[d2rloader]\n# Launch this mod automatically.\ndefault_mod = \"\"  # comment\ncheck_for_updates = true\n\n[d2rloader.backups]\nenabled = true\n";
	std::string u = dl::SetTomlKey(t, "d2rloader", "default_mod", "\"" DL_MOD_FOLDER "\"");
	Check(dl::GetTomlKey(u, "d2rloader", "default_mod") == "\"" DL_MOD_FOLDER "\"" && u.find("# comment") != std::string::npos, "replace keeps the comment");
	u = dl::SetTomlKey(u, "d2rloader", "check_for_updates", "false");
	Check(dl::GetTomlKey(u, "d2rloader", "check_for_updates") == "false", "replace a bool");
	Check(dl::GetTomlKey(u, "d2rloader.backups", "enabled") == "true", "other section untouched");
	u = dl::SetTomlKey(u, "d2rloader", "skip_title_screen", "true");
	Check(dl::GetTomlKey(u, "d2rloader", "skip_title_screen") == "true" && dl::GetTomlKey(u, "d2rloader.backups", "skip_title_screen").empty(),
	      "a missing key is added in its own section");
	u = dl::SetTomlKey(u, "d2rcore.stash", "add_shared_tabs", "7");
	Check(dl::GetTomlKey(u, "d2rcore.stash", "add_shared_tabs") == "7", "a missing section is appended");
	std::string c = "[a]\r\nx = 1\r\n";
	std::string cu = dl::SetTomlKey(c, "a", "x", "2");
	Check(cu == "[a]\r\nx = 2\r\n", "CRLF kept");
	Check(dl::SetTomlKey("[a]\nx = 1", "a", "x", "2") == "[a]\nx = 2", "no final newline stays without one");
	Check(dl::SetTomlKey("[a]\n# x = 1\n", "a", "x", "2") == "[a]\nx = 2\n", "commented template replaced");
	Check(dl::GetTomlKey("[a]\ns = \"a # b\" # c\n", "a", "s") == "\"a # b\"", "# inside quotes is no comment");
}

// docs/downloader.md "Environment warnings": D2RLoader's default mod and stray copies of the plugin.
void TestEnvironment() {
	const std::string bs(1, char(92));
	std::string toml = "[d2rloader]\n# Launch this mod automatically.\ndefault_mod = \"OtherMod\"  # set by the gear\nlaunch_arguments = \"-w\"\n\n[d2rcore.stash]\nadd_shared_tabs = 3\n";
	dl::DefaultModCheck c = dl::CheckDefaultMod(toml, true, DL_MOD_FOLDER);
	Check(!c.ok && c.mod == "OtherMod" && !c.configMissing, "default_mod OtherMod is not ours");
	Check(dl::DefaultModWarning(c, DL_MOD_FOLDER) == "D2RLoader launches OtherMod instead of " DL_MOD_FOLDER, "warning names the mod D2RLoader starts");
	std::string fixed = dl::SetDefaultMod(toml, DL_MOD_FOLDER);
	Check(dl::CheckDefaultMod(fixed, true, DL_MOD_FOLDER).ok, "fixed config names " DL_MOD_FOLDER);
	Check(fixed == "[d2rloader]\n# Launch this mod automatically.\ndefault_mod = \"" DL_MOD_FOLDER "\" # set by the gear\nlaunch_arguments = \"-w\"\n\n[d2rcore.stash]\nadd_shared_tabs = 3\n",
	      "only default_mod changes");
	Check(dl::CheckDefaultMod("[d2rloader]\ndefault_mod = 'mymod'\n", true, DL_MOD_FOLDER).ok, "single quotes, any case");
	Check(dl::DefaultModWarning(dl::CheckDefaultMod("[d2rloader]\ndefault_mod = \"" DL_MOD_FOLDER "\"\n", true, DL_MOD_FOLDER), DL_MOD_FOLDER).empty(), "no warning when ours");
	dl::DefaultModCheck none = dl::CheckDefaultMod("[d2rloader]\ndefault_mod = \"\"\n", true, DL_MOD_FOLDER);
	Check(!none.ok && none.mod.empty() && dl::DefaultModWarning(none, DL_MOD_FOLDER).find("default_mod is empty") != std::string::npos, "empty default_mod");
	Check(!dl::CheckDefaultMod("[d2rloader]\n# default_mod = \"" DL_MOD_FOLDER "\"\n", true, DL_MOD_FOLDER).ok, "a commented template is no default mod");
	Check(!dl::CheckDefaultMod("[other]\ndefault_mod = \"" DL_MOD_FOLDER "\"\n", true, DL_MOD_FOLDER).ok, "default_mod of another section does not count");
	dl::DefaultModCheck missing = dl::CheckDefaultMod("", false, DL_MOD_FOLDER);
	Check(!missing.ok && missing.configMissing && dl::DefaultModWarning(missing, DL_MOD_FOLDER).find("No D2RLoader settings file") != std::string::npos, "no config file");
	Check(dl::CheckDefaultMod(dl::SetDefaultMod("", DL_MOD_FOLDER), true, DL_MOD_FOLDER).ok, "a missing config gets the section and key");
	std::string bom = "\xEF\xBB\xBF[d2rloader]\r\ndefault_mod = \"Old\"\r\n";
	Check(!dl::CheckDefaultMod(bom, true, DL_MOD_FOLDER).ok && dl::CheckDefaultMod(bom, true, DL_MOD_FOLDER).mod == "Old", "a BOM before the section");
	Check(dl::SetDefaultMod(bom, DL_MOD_FOLDER) == "\xEF\xBB\xBF[d2rloader]\r\ndefault_mod = \"" DL_MOD_FOLDER "\"\r\n", "BOM and CRLF kept");
	Check(dl::TomlUnquote(" \"a b\" ") == "a b" && dl::TomlUnquote("x") == "x" && dl::TomlUnquote("\"") == "\"", "TomlUnquote");

	Check(dl::IsOurPluginFile(DL_PLUGIN_DLL_PREFIX ".dll", "", "") && dl::IsOurPluginFile(DL_PLUGIN_DLL_PREFIX "-0.68.dll", "", "") && dl::IsOurPluginFile(DL_PLUGIN_DLL_PREFIX " (1).dll", "", ""),
	      "plugin by file name");
	Check(dl::IsOurPluginFile("renamed.dll", DL_PLUGIN_PRODUCT_NAME, "") && dl::IsOurPluginFile("x.dll", "", DL_PLUGIN_PRODUCT_NAME " core plugin (" DL_PLUGIN_DLL_PREFIX ")"), "plugin by version resource");
	Check(!dl::IsOurPluginFile("loot.dll", "Loot Filter", "Loot filter plugin") && !dl::IsOurPluginFile(DL_PLUGIN_DLL_PREFIX ".txt", "", "") &&
	          !dl::IsOurPluginFile(DL_PLUGIN_DLL_PREFIX "-off.txt", "", "") && !dl::IsOurPluginFile(DL_EXE_NAME ".exe", DL_PLUGIN_PRODUCT_NAME, ""),
	      "other plugins, text files and exes are not ours");

	std::string mod;
	Check(dl::PluginPlaceOf("d2rloader" + bs + "plugins" + bs + DL_PLUGIN_DLL_PREFIX ".dll", DL_MOD_FOLDER) == dl::PluginPlace::GameWide, "game-wide plugins folder");
	Check(dl::PluginPlaceOf("mods/OtherMod/d2rloader/plugins/" DL_PLUGIN_DLL_PREFIX ".dll", DL_MOD_FOLDER, &mod) == dl::PluginPlace::OtherMod && mod == "OtherMod",
	      "another mod's plugins folder, its name as written");
	Check(dl::PluginPlaceOf("MODS" + bs + "mymod" + bs + "D2RLoader" + bs + "Plugins" + bs + DL_PLUGIN_DLL_PREFIX ".dll", DL_MOD_FOLDER) == dl::PluginPlace::OurMod,
	      "our own mod folder, any case");
	Check(dl::PluginPlaceOf("mods/" DL_MOD_FOLDER "/" DL_PLUGIN_DLL_PREFIX ".dll", DL_MOD_FOLDER) == dl::PluginPlace::None && dl::PluginPlaceOf(DL_PLUGIN_DLL_PREFIX ".dll", DL_MOD_FOLDER) == dl::PluginPlace::None &&
	          dl::PluginPlaceOf("mods/../d2rloader/plugins/x.dll", DL_MOD_FOLDER) == dl::PluginPlace::None,
	      "not a plugins folder");
	std::string g = dl::StrayPluginWarning("C:" + bs + "D2R" + bs + "d2rloader" + bs + "plugins" + bs + DL_PLUGIN_DLL_PREFIX ".dll", dl::PluginPlace::GameWide, "");
	Check(g == "A leftover " DL_MOD_NAME " plugin at C:" + bs + "D2R" + bs + "d2rloader" + bs + "plugins" + bs + DL_PLUGIN_DLL_PREFIX ".dll is loaded for every mod; move it out.",
	      "game-wide warning text");
	Check(dl::StrayPluginWarning("p", dl::PluginPlace::OtherMod, "OtherMod").find("launches OtherMod") != std::string::npos &&
	          dl::StrayPluginWarning("p", dl::PluginPlace::OtherMod, "OtherMod", true) == "Stray " DL_MOD_NAME " plugin in mods" + bs + "OtherMod (loaded with that mod)",
	      "other-mod warning, long and brief");
	Check(dl::StrayPluginWarning("p", dl::PluginPlace::OurMod, DL_MOD_FOLDER).empty() && dl::StrayPluginWarning("p", dl::PluginPlace::None, "").empty(),
	      "no warning for the installed plugin");
	Check(dl::RemovedPluginPath("C:" + bs + "L" + bs + DL_MOD_FOLDER, "2030-01-31", "d2rloader/plugins/" DL_PLUGIN_DLL_PREFIX ".dll") ==
	          "C:" + bs + "L" + bs + DL_MOD_FOLDER + bs + "removed" + bs + "2030-01-31" + bs + "d2rloader" + bs + "plugins" + bs + DL_PLUGIN_DLL_PREFIX ".dll",
	      "moved under removed, date, relative path");
}

}  // namespace

int main() {
	std::setvbuf(stdout, nullptr, _IONBF, 0);  // a crash must not swallow the lines printed so far
	TestSha256();
	TestJson();
	TestSafePath();
	TestManifest();
	TestHosting();
	TestPlan();
	TestVersions();
	TestDetect();
	TestPaths();
	TestSevenZip();
	TestCoexist();
	TestGameProcessName();
	TestToml();
	TestEnvironment();
	std::printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
