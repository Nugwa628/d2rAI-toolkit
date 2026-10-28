// The downloader's window. Everything about its look comes from downloader_config.h: colours, fonts, texts, and the
// optional logo / background pictures that CMake embeds (-DDL_LOGO_FILE=..., -DDL_BACKGROUND_FILE=...). Without
// pictures the window is a plain colour and the mod name is drawn as the logo. There is no Play button: the player
// starts D2RLoader.exe.
//
// Plain Win32 + GDI+ (part of Windows): GDI+ decodes the pictures (every GIF frame) and draws anti-aliased text and
// paths. The buttons are real BUTTON windows (BS_OWNERDRAW), so Tab, Space and screen readers work; they are drawn
// over the same background. Everything is laid out on a 900 x 600 canvas and scaled to the monitor's DPI.
#include "window.h"
#include "../core/config.h"

#include "game.h"
#include "util.h"

#include <windows.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <shellapi.h>
#include <shlwapi.h>
#include <shobjidl.h>

#include <algorithm>
#include <cmath>
#include <memory>
#include <thread>
#include <vector>

namespace Gdiplus {
using std::max;  // gdiplus.h needs min / max, which NOMINMAX takes away
using std::min;
}  // namespace Gdiplus
#include <objidl.h>
#include <gdiplus.h>

namespace dl {

namespace {

namespace gp = Gdiplus;

// Resource ids: written by CMake into the generated resource script (optional pictures). 10 = RT_RCDATA.
enum : int { ResBackground = 101, ResLogo };

constexpr float CanvasW = 900, CanvasH = 600;
const gp::RectF LogoRect(256, 12, 388, 134);
const gp::RectF PanelRect(200, 158, 500, 170);
const gp::RectF StatusRect(240, 182, 420, 76);  // four lines
const gp::RectF LineRect(240, 259, 420, 20);
const gp::RectF HowToPlayRect(110, 512, 680, 40);  // two lines
const gp::RectF BarRect(242, 285, 416, 9);

gp::Color Rgb(unsigned rgb, int alpha = 255) { return gp::Color(BYTE(alpha), BYTE(rgb >> 16), BYTE(rgb >> 8), BYTE(rgb)); }
// Scaled copy of an 0xRRGGBB colour (the button fills are darker shades of the accent colour).
gp::Color Shade(unsigned rgb, float k, int alpha = 255) {
	return gp::Color(BYTE(alpha), BYTE(std::min(255.f, float((rgb >> 16) & 255) * k)), BYTE(std::min(255.f, float((rgb >> 8) & 255) * k)),
	                 BYTE(std::min(255.f, float(rgb & 255) * k)));
}
const gp::Color Gold = Rgb(DL_COLOR_ACCENT), GoldBright = Rgb(DL_COLOR_ACCENT_BRIGHT), Body = Rgb(DL_COLOR_TEXT), Dim = Rgb(DL_COLOR_DIM),
	ErrorText = Rgb(DL_COLOR_ERROR);

enum : int { IdInstall = 101, IdRepair, IdUninstall, IdMods, IdIssues, IdChoose, IdCancel, IdLoader, IdSelfUpdate, IdDefaultMod,
               IdMovePlugins };
enum Kind { Big, Small, Icon };
struct Button {
	int id;
	std::wstring label;  // also the window text (screen readers, tooltips)
	Kind kind;
	gp::RectF r;
	HWND h = nullptr;
	bool hot = false;
};
Button buttons[] = {
	{IdInstall, L"Install", Big, gp::RectF(330, 346, 240, 66)},
	{IdCancel, L"Cancel", Big, gp::RectF(330, 346, 240, 66)},
	{IdRepair, L"Repair", Small, gp::RectF(305, 426, 140, 34)},  // x moves with Download D2RLoader (RefreshStatus)
	{IdUninstall, L"Uninstall", Small, gp::RectF(455, 426, 140, 34)},
	{IdLoader, L"Download D2RLoader", Small, gp::RectF(504, 426, 192, 34)},  // shown when D2RLoader is missing or another version
	{IdDefaultMod, L"Make default", Small, gp::RectF(0, 426, 168, 34)},   // shown when D2RLoader's default_mod is not the mod's folder
	{IdMovePlugins, L"Move old plugin", Small, gp::RectF(0, 426, 168, 34)},  // shown when an old copy of the plugin is elsewhere
	{IdChoose, L"Game folder", Small, gp::RectF(278, 468, 168, 34)},
	{IdMods, L"Open mods", Small, gp::RectF(456, 468, 168, 34)},
	{IdIssues, L"Report an issue", Icon, gp::RectF(858, 556, 28, 28)},
};

constexpr UINT WmProgress = WM_APP + 1, WmDone = WM_APP + 2;
constexpr UINT_PTR AnimTimer = 1;

struct ProgressMsg {
	std::string text;
	double fraction;
	int action = 0;  // WmDone: the finished action (0: the start-up check)
};

struct Art {
	std::unique_ptr<gp::Bitmap> background;
	std::vector<std::unique_ptr<gp::Bitmap>> logo;  // every frame, already scaled to the window (empty: the name is drawn)
	std::vector<std::unique_ptr<gp::CachedBitmap>> logoCached;
	std::vector<UINT> delays;  // ms per frame
	std::unique_ptr<gp::FontFamily> title, body;
};

struct Ui {
	HINSTANCE inst = nullptr;
	HWND wnd = nullptr, tip = nullptr;
	float s = 1;  // canvas -> pixels
	int w = 0, h = 0;
	HDC sceneDC = nullptr, backDC = nullptr;  // the static layer (background, panel, footer) and the back buffer
	HBITMAP sceneBmp = nullptr, backBmp = nullptr, sceneOld = nullptr, backOld = nullptr;
	std::unique_ptr<Art> art;
	size_t frame = 0;
	ULONGLONG frameAt = 0;
	std::string status, line = "Looking for a new release ...";
	bool lineError = false;
	double fraction = -2;  // 0..1, -1 busy (moving bar), < -1 no bar
	int marquee = 0;
	std::unique_ptr<Launcher> launcher;
	Launcher::View view;
	Options opt;
	std::wstring snapshot;
	bool busy = false;
	std::thread worker;
};
Ui g;

int Px(float v) { return int(std::lround(v * g.s)); }

Button* FindButton(int id) {
	for (auto& b : buttons)
		if (b.id == id)
			return &b;
	return nullptr;
}

// ---------------------------------------------------------------- art

std::pair<const void*, DWORD> Resource(int id) {
	HRSRC r = FindResourceW(g.inst, MAKEINTRESOURCEW(id), MAKEINTRESOURCEW(10));  // 10 = RT_RCDATA
	if (!r)
		return {nullptr, 0};
	HGLOBAL h = LoadResource(g.inst, r);
	return {h ? LockResource(h) : nullptr, SizeofResource(g.inst, r)};
}

// A new 32-bit premultiplied bitmap of `src` (or of its current frame), scaled to w x h. GDI+ reads an image from its
// stream lazily, so nothing that came from a stream is kept.
std::unique_ptr<gp::Bitmap> Copy(gp::Image& src, int w, int h) {
	auto out = std::make_unique<gp::Bitmap>(w, h, PixelFormat32bppPARGB);
	gp::Graphics gr(out.get());
	gr.SetInterpolationMode(gp::InterpolationModeHighQualityBicubic);
	gr.SetPixelOffsetMode(gp::PixelOffsetModeHalf);
	gr.DrawImage(&src, gp::Rect(0, 0, w, h), 0, 0, int(src.GetWidth()), int(src.GetHeight()), gp::UnitPixel);
	return out;
}

// Loads resource `id` as an image; for each frame calls `use`. Returns the number of frames (0: not loaded).
template <class F>
UINT ForEachFrame(int id, F use) {
	auto [data, size] = Resource(id);
	if (!data)
		return 0;
	IStream* stream = SHCreateMemStream(static_cast<const BYTE*>(data), size);
	if (!stream)
		return 0;
	UINT frames = 0;
	{
		gp::Bitmap img(stream);
		if (img.GetLastStatus() == gp::Ok) {
			GUID dim = gp::FrameDimensionTime;
			frames = std::max(1u, img.GetFrameCount(&dim));
			std::vector<UINT> delays(frames, 100);
			UINT bytes = img.GetPropertyItemSize(PropertyTagFrameDelay);
			if (bytes) {
				std::vector<uint8_t> buf(bytes);
				auto* item = reinterpret_cast<gp::PropertyItem*>(buf.data());
				if (img.GetPropertyItem(PropertyTagFrameDelay, bytes, item) == gp::Ok)
					for (UINT i = 0; i < frames && (i + 1) * 4 <= item->length; ++i)
						delays[i] = std::max(20u, static_cast<UINT*>(item->value)[i] * 10);  // 1/100 s
			}
			for (UINT i = 0; i < frames; ++i) {
				if (frames > 1)
					img.SelectActiveFrame(&dim, i);
				use(img, delays[i]);
			}
		}
	}
	stream->Release();
	return frames;
}

std::unique_ptr<gp::FontFamily> LoadFont(const wchar_t* name, const wchar_t* fallback) {
	auto fam = std::make_unique<gp::FontFamily>(name);
	if (fam->GetLastStatus() == gp::Ok)
		return fam;
	Log("window: font " + Utf8(name) + " not found, using " + Utf8(fallback));
	return std::make_unique<gp::FontFamily>(fallback);
}

void LoadArt() {
	Art& a = *g.art;
	ForEachFrame(ResBackground, [&](gp::Image& img, UINT) { a.background = Copy(img, g.w, g.h); });
	ForEachFrame(ResLogo, [&](gp::Image& img, UINT delay) {
		a.logo.push_back(Copy(img, Px(LogoRect.Width), Px(LogoRect.Height)));
		a.delays.push_back(delay);
	});
	a.title = LoadFont(DL_FONT_TITLE, L"Georgia");
	a.body = LoadFont(DL_FONT_BODY, L"Segoe UI");
	Log("window: art loaded, logo " + std::to_string(a.logo.size()) + " frames");
}

// ---------------------------------------------------------------- drawing (canvas coordinates)

void Quality(gp::Graphics& gr) {
	gr.SetSmoothingMode(gp::SmoothingModeAntiAlias);
	gr.SetTextRenderingHint(gp::TextRenderingHintAntiAliasGridFit);
	gr.SetInterpolationMode(gp::InterpolationModeHighQualityBicubic);
	gr.SetPixelOffsetMode(gp::PixelOffsetModeHalf);
}

void AddRoundRect(gp::GraphicsPath& p, const gp::RectF& r, float rad) {
	float d = rad * 2;
	p.AddArc(r.X, r.Y, d, d, 180, 90);
	p.AddArc(r.X + r.Width - d, r.Y, d, d, 270, 90);
	p.AddArc(r.X + r.Width - d, r.Y + r.Height - d, d, d, 0, 90);
	p.AddArc(r.X, r.Y + r.Height - d, d, d, 90, 90);
	p.CloseFigure();
}

enum Trim { TrimEnd, TrimPath, Wrap };
void Text(gp::Graphics& gr, const std::wstring& t, const gp::FontFamily& fam, float px, const gp::Color& c, const gp::RectF& r,
          gp::StringAlignment align = gp::StringAlignmentNear, Trim trim = TrimEnd, bool shadow = true) {
	gp::Font font(&fam, px, gp::FontStyleRegular, gp::UnitPixel);
	gp::StringFormat f;
	f.SetAlignment(align);
	f.SetLineAlignment(gp::StringAlignmentCenter);
	if (trim != Wrap)
		f.SetFormatFlags(gp::StringFormatFlagsNoWrap);
	f.SetTrimming(trim == TrimPath ? gp::StringTrimmingEllipsisPath : gp::StringTrimmingEllipsisCharacter);
	if (shadow) {  // keeps light text readable on the brighter parts of the art
		gp::SolidBrush sb(gp::Color(170, 0, 0, 0));
		gp::RectF sr(r.X + 1, r.Y + 1, r.Width, r.Height);
		gr.DrawString(t.c_str(), INT(t.size()), &font, sr, &f, &sb);
	}
	gp::SolidBrush b(c);
	gr.DrawString(t.c_str(), INT(t.size()), &font, r, &f, &b);
}

// The part that never changes: background, the panel, how to play, the footer.
void DrawScene(gp::Graphics& gr) {
	if (g.art->background)
		gr.DrawImage(g.art->background.get(), 0, 0);
	else
		gr.Clear(Rgb(DL_COLOR_BACKGROUND));
	gr.ScaleTransform(g.s, g.s);
	{
		gp::GraphicsPath p;
		AddRoundRect(p, PanelRect, 8.f);
		gp::SolidBrush fill(Rgb(DL_COLOR_PANEL, 235));
		gr.FillPath(&fill, &p);
		gp::Pen edge(Shade(DL_COLOR_ACCENT, 0.5f), 1);
		gr.DrawPath(&edge, &p);
	}
	Text(gr, Wide(HowToPlay), *g.art->body, 14, Gold, HowToPlayRect, gp::StringAlignmentCenter, Wrap);
	std::wstring footer = L"Downloader " + Wide(LauncherVersion) + L"  ·  " DL_W(DL_FOOTER);
	Text(gr, footer, *g.art->body, 12, Dim, gp::RectF(14, 560, 830, 24));
	gr.ResetTransform();
}

// The part that changes: the logo frame, status, progress. `cached`: the logo through CachedBitmaps made for the
// window's device (the snapshot draws into a bitmap instead).
void DrawDynamic(gp::Graphics& gr, bool cached = true) {
	if (g.art->logo.empty()) {  // no picture: the mod's name is the logo
		gr.ScaleTransform(g.s, g.s);
		Quality(gr);
		Text(gr, DL_W(DL_MOD_NAME), *g.art->title, 44, Gold, LogoRect, gp::StringAlignmentCenter);
		gr.ResetTransform();
	} else if (!cached) {
		gr.DrawImage(g.art->logo[g.frame].get(), Px(LogoRect.X), Px(LogoRect.Y));
	} else {
		if (g.art->logoCached.size() != g.art->logo.size()) {  // a CachedBitmap belongs to the device it was made for
			g.art->logoCached.clear();
			for (auto& f : g.art->logo)
				g.art->logoCached.push_back(std::make_unique<gp::CachedBitmap>(f.get(), &gr));
		}
		gr.DrawCachedBitmap(g.art->logoCached[g.frame].get(), Px(LogoRect.X), Px(LogoRect.Y));
	}
	gr.ScaleTransform(g.s, g.s);
	Quality(gr);
	// status: "Name: value" lines; long paths are shortened in the middle. Four lines fit; with more (the environment warnings)
	// the mod folder line goes and the rest get smaller.
	std::vector<std::wstring> lines;
	std::wstring st = Wide(g.status);
	for (size_t start = 0; start <= st.size();) {
		size_t nl = st.find(L'\n', start);
		lines.push_back(st.substr(start, nl == std::wstring::npos ? std::wstring::npos : nl - start));
		if (nl == std::wstring::npos)
			break;
		start = nl + 1;
	}
	if (lines.size() > 4)
		lines.erase(std::remove_if(lines.begin(), lines.end(), [](const std::wstring& l) { return l.rfind(L"Mod folder:", 0) == 0; }), lines.end());
	size_t rows = std::min<size_t>(lines.size(), 5);
	float rowH = rows > 4 ? StatusRect.Height / float(rows) : 19.f, size = rows > 4 ? 12.f : 14.f;
	for (size_t row = 0; row < rows; ++row) {
		const std::wstring& l = lines[row];
		gp::RectF r(StatusRect.X, StatusRect.Y + float(row) * rowH, StatusRect.Width, rowH);
		bool warn = !l.empty() && l[0] == L'!';  // StatusText marks warnings with "!"
		Text(gr, warn ? l.substr(1) : l, *g.art->body, size, warn ? ErrorText : Body, r, gp::StringAlignmentNear, warn ? TrimEnd : TrimPath);
	}
	Text(gr, Wide(g.line), *g.art->body, 14, g.lineError ? ErrorText : Gold, LineRect, gp::StringAlignmentCenter);
	if (g.fraction >= -1) {
		gp::SolidBrush track(gp::Color(170, 0, 0, 0));
		gr.FillRectangle(&track, BarRect);
		float x = BarRect.X, w = BarRect.Width;
		if (g.fraction >= 0) {
			w = float(std::clamp(g.fraction, 0.0, 1.0)) * BarRect.Width;
		} else {  // busy, no amount known: a moving piece
			float piece = BarRect.Width / 4, run = BarRect.Width + piece;
			x = BarRect.X - piece + std::fmod(g.marquee * 6.f, run);
			w = piece;
			float l = std::max(x, BarRect.X), r = std::min(x + w, BarRect.X + BarRect.Width);
			x = l, w = std::max(0.f, r - l);
		}
		if (w > 0.5f) {
			gp::RectF fill(x, BarRect.Y, w, BarRect.Height);
			gp::LinearGradientBrush lb(gp::PointF(0, BarRect.Y), gp::PointF(0, BarRect.Y + BarRect.Height), GoldBright, Shade(DL_COLOR_ACCENT, 0.7f));
			gr.FillRectangle(&lb, fill);
		}
		gp::Pen frame(Shade(DL_COLOR_ACCENT, 0.5f), 1);
		gr.DrawRectangle(&frame, BarRect);
	}
	gr.ResetTransform();
}

// The "report an issue" icon: a speech bubble with an exclamation mark, drawn with paths (no picture needed).
void DrawIcon(gp::Graphics& gr, const gp::RectF& r, const gp::Color& c) {
	gp::GraphicsPath p;
	gp::RectF body(r.X, r.Y, r.Width, r.Height * 0.74f);
	AddRoundRect(p, body, r.Width * 0.22f);
	gp::PointF tail[] = {{r.X + r.Width * 0.22f, body.GetBottom() - 1}, {r.X + r.Width * 0.18f, r.Y + r.Height},
	                     {r.X + r.Width * 0.5f, body.GetBottom() - 1}};
	p.AddPolygon(tail, 3);
	gp::SolidBrush b(c);
	gr.FillPath(&b, &p);
	gp::Font f(g.art->title.get(), r.Height * 0.55f, gp::FontStyleBold, gp::UnitPixel);
	gp::StringFormat sf;
	sf.SetAlignment(gp::StringAlignmentCenter);
	sf.SetLineAlignment(gp::StringAlignmentCenter);
	gp::SolidBrush ink(Rgb(DL_COLOR_BACKGROUND));
	gr.DrawString(L"!", 1, &f, body, &sf, &ink);
}

void DrawButton(gp::Graphics& gr, const Button& b, bool pressed, bool disabled, bool focus) {
	gp::RectF r = b.r;
	if (pressed)
		r.Offset(1, 1);
	bool hot = b.hot && !disabled;
	if (b.kind == Icon) {
		DrawIcon(gr, gp::RectF(r.X + 3, r.Y + 3, r.Width - 6, r.Height - 6), hot ? GoldBright : Gold);
	} else {
		gp::RectF box(r.X + 1, r.Y + 1, r.Width - 2, r.Height - 2);
		gp::GraphicsPath p;
		AddRoundRect(p, box, b.kind == Big ? 7.f : 4.f);
		if (b.kind == Big) {
			gp::Color top = disabled ? Shade(DL_COLOR_DIM, 0.35f, 225) : hot ? Shade(DL_COLOR_ACCENT, 0.55f, 235) : Shade(DL_COLOR_ACCENT, 0.42f, 230);
			gp::Color bottom = disabled ? Shade(DL_COLOR_DIM, 0.2f, 225) : hot ? Shade(DL_COLOR_ACCENT, 0.3f, 235) : Shade(DL_COLOR_ACCENT, 0.2f, 230);
			gp::LinearGradientBrush fill(gp::PointF(0, box.Y), gp::PointF(0, box.Y + box.Height), top, bottom);
			gr.FillPath(&fill, &p);
			gp::Pen edge(disabled ? Dim : hot ? GoldBright : Gold, 2);
			gr.DrawPath(&edge, &p);
		} else {
			gp::SolidBrush fill(gp::Color(hot ? 190 : 160, 0, 0, 0));
			gr.FillPath(&fill, &p);
			gp::Pen edge(disabled ? Shade(DL_COLOR_DIM, 0.5f) : hot ? Gold : Shade(DL_COLOR_ACCENT, 0.6f), 1);
			gr.DrawPath(&edge, &p);
		}
		Text(gr, b.label, *g.art->title, b.kind == Big ? 30.f : 15.f, disabled ? Dim : hot ? GoldBright : Gold, r,
		     gp::StringAlignmentCenter);
	}
	if (focus) {
		gp::Pen dots(gp::Color(200, 232, 196, 128), 1);
		dots.SetDashStyle(gp::DashStyleDot);
		gr.DrawRectangle(&dots, gp::RectF(r.X - 2, r.Y - 2, r.Width + 3, r.Height + 3));
	}
}

// The whole window into one bitmap (--snapshot): the same functions as the real painting.
bool Snapshot(const std::wstring& file) {
	gp::Bitmap bmp(g.w, g.h, PixelFormat32bppARGB);
	{
		gp::Graphics gr(&bmp);
		HDC dc = gr.GetHDC();
		BitBlt(dc, 0, 0, g.w, g.h, g.sceneDC, 0, 0, SRCCOPY);
		gr.ReleaseHDC(dc);
		DrawDynamic(gr, false);
		gr.ScaleTransform(g.s, g.s);
		Quality(gr);
		for (const auto& b : buttons)
			if (GetWindowLongPtrW(b.h, GWL_STYLE) & WS_VISIBLE)  // the window itself is hidden
				DrawButton(gr, b, false, !IsWindowEnabled(b.h), false);
	}
	CLSID png{0x557cf406, 0x1a04, 0x11d3, {0x9a, 0x73, 0x00, 0x00, 0xf8, 0x1e, 0xf3, 0x2e}};
	bool ok = bmp.Save(file.c_str(), &png, nullptr) == gp::Ok;
	Log("window: snapshot " + Utf8(file) + (ok ? " written" : " FAILED"));
	return ok;
}

// ---------------------------------------------------------------- state

void SetBusy(bool busy) {
	g.busy = busy;
	for (auto& b : buttons) {
		if (b.id == IdIssues)
			continue;
		if (b.id == IdInstall || b.id == IdCancel) {
			ShowWindow(b.h, (b.id == IdCancel) == busy ? SW_SHOWNA : SW_HIDE);  // Stop takes Install's place
			continue;
		}
		EnableWindow(b.h, !busy);
	}
}

void InvalidatePanel() {
	RECT r{Px(PanelRect.X), Px(PanelRect.Y), Px(PanelRect.X + PanelRect.Width), Px(PanelRect.Y + PanelRect.Height)};
	InvalidateRect(g.wnd, &r, FALSE);
}

void MoveButtonX(int id, float x) {
	Button* b = FindButton(id);
	b->r.X = x;
	SetWindowPos(b->h, nullptr, Px(b->r.X), Px(b->r.Y), 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
}

// The window's state from Launcher::Inspect: the status lines, the big button's label, Download D2RLoader.
void RefreshStatus() {
	g.view = g.launcher->Inspect();
	const Launcher::View& v = g.view;
	g.status = g.launcher->StatusText(v);
	Button* b = FindButton(IdInstall);
	b->label = v.foreignMod || v.installed.empty() ? L"Install" : v.updateAvailable || !v.feedOk ? L"Update" : L"Up to date";
	SetWindowTextW(b->h, b->label.c_str());
	EnableWindow(b->h, !g.busy && v.d2rFound && b->label != L"Up to date");
	EnableWindow(FindButton(IdUninstall)->h, !g.busy && !v.installed.empty());
	EnableWindow(FindButton(IdRepair)->h, !g.busy && !v.installed.empty());
	bool loader = v.d2rFound && !v.loaderOk;
	bool defaultMod = v.d2rFound && v.defaultModChecked && !v.defaultMod.ok, plugins = v.d2rFound && !v.strayPlugins.empty();
	// the row stays centred with the buttons that are needed (Download D2RLoader, Make default, Move old plugin)
	std::vector<std::pair<int, bool>> row = {{IdRepair, true}, {IdUninstall, true}, {IdLoader, loader}, {IdDefaultMod, defaultMod}, {IdMovePlugins, plugins}};
	float width = -10;
	for (const auto& [id, shown] : row)
		width += shown ? FindButton(id)->r.Width + 10 : 0;
	float x = (CanvasW - width) / 2;
	for (const auto& [id, shown] : row) {
		if (shown) {
			MoveButtonX(id, x);
			x += FindButton(id)->r.Width + 10;
		}
		if (id != IdRepair && id != IdUninstall)
			ShowWindow(FindButton(id)->h, shown ? SW_SHOWNA : SW_HIDE);
	}
	InvalidateRect(b->h, nullptr, FALSE);
	InvalidatePanel();
}

void RunAction(int id) {
	if (g.busy)
		return;
	if (id == IdUninstall) {
		std::wstring q = L"Remove " DL_W(DL_MOD_NAME) L" (" + g.launcher->ModDir() +
		                 L")?\n\nThe downloader deletes only this folder. Saved characters, the game, D2RLoader and any other mods stay as they are.";
		if (MessageBoxW(g.wnd, q.c_str(), L"Uninstall " DL_W(DL_MOD_NAME), MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES)
			return;
	}
	if (id == IdInstall && g.view.build.verdict != BuildVerdict::Ok) {  // allowed, after a clear warning
		std::wstring q = L"The installed D2R is " + (g.view.build.version.empty() ? std::wstring(L"of an unknown version") : L"version " + Wide(g.view.build.version)) +
		                 L"; " DL_W(DL_MOD_NAME) L" is made for D2R " + Wide(g.view.requiredVersion) +
		                 L" and will not run until the game matches.\n\nInstall " DL_W(DL_MOD_NAME) L" anyway?";
		if (MessageBoxW(g.wnd, q.c_str(), DL_W(DL_PRODUCT_TITLE), MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES)
			return;
	}
	if (id == IdDefaultMod) {
		std::wstring q = Wide(g.launcher->EnvironmentWarnings(g.view).front()) + L"\n\nSet " DL_W(DL_MOD_NAME) L" as D2RLoader's default mod? Only the line "
		                 L"default_mod = \"" DL_W(DL_MOD_FOLDER) L"\" in " + g.view.loaderConfig + L" is written; the rest of the file stays the same. "
		                 L"D2RLoader's own settings (the gear icon) can change it back at any time.";
		if (MessageBoxW(g.wnd, q.c_str(), L"Set the default mod", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES)
			return;
	}
	if (id == IdMovePlugins) {
		std::wstring list;
		for (const auto& sp : g.view.strayPlugins)
			list += Wide(StrayPluginWarning(Utf8(sp.path), sp.place, sp.mod)) + L"\n";
		std::wstring q = list + L"\nMove " + (g.view.strayPlugins.size() == 1 ? std::wstring(L"it") : L"them") + L" to " +
		                 JoinPath(g.launcher->options().dataDir, L"removed") + LR"(\<today>\? The files are kept there, not deleted.)";
		if (MessageBoxW(g.wnd, q.c_str(), L"Old " DL_W(DL_MOD_NAME) L" plugin found", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES)
			return;
	}
	g.launcher->cancel = false;
	SetBusy(true);
	if (g.worker.joinable())
		g.worker.join();
	g.worker = std::thread([id] {
		std::string err;
		bool ok = false;
		Launcher& l = *g.launcher;
		switch (id) {
		case IdInstall: ok = l.InstallOrUpdate(err); break;
		case IdRepair: ok = l.Repair(err); break;
		case IdUninstall: ok = l.Uninstall(err); break;
		case IdSelfUpdate: ok = l.SelfUpdate(err); break;
		case IdDefaultMod: ok = l.FixDefaultMod(err); break;
		case IdMovePlugins: ok = l.RemoveStrayPlugins(err); break;
		}
		std::string done = id == IdInstall || id == IdRepair ? "Finished. " + std::string(HowToPlay) : "Finished.";
		auto* msg = new ProgressMsg{ok ? done : err, ok ? 1.0 : -2.0, id};
		PostMessageW(g.wnd, WmDone, WPARAM(ok ? 1 : 0), reinterpret_cast<LPARAM>(msg));
	});
}

// mods\the mod exists without the downloader's marker: replace it only when the player says so.
void AskReplace(const std::string& text) {
	if (MessageBoxW(g.wnd, Wide(text).c_str(), DL_W(DL_PRODUCT_TITLE), MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) != IDYES)
		return;
	g.launcher->SetReplace(true);
	RunAction(IdInstall);
}

void ChooseSource() {
	IFileOpenDialog* dlg = nullptr;
	if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dlg))))
		return;
	DWORD flags = 0;
	dlg->GetOptions(&flags);
	dlg->SetOptions(flags | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM);
	dlg->SetTitle(L"Select your Diablo II: Resurrected install folder (it contains D2R.exe)");
	if (SUCCEEDED(dlg->Show(g.wnd))) {
		IShellItem* item = nullptr;
		if (SUCCEEDED(dlg->GetResult(&item))) {
			PWSTR path = nullptr;
			if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path))) {
				std::string err;
				if (!g.launcher->SetD2RDir(path, err))
					MessageBoxW(g.wnd, Wide(err).c_str(), DL_W(DL_PRODUCT_TITLE), MB_ICONWARNING);
				RefreshStatus();
				CoTaskMemFree(path);
			}
			item->Release();
		}
	}
	dlg->Release();
}

// The new downloader after a self-update: the same exe path and arguments.
bool Restart() {
	std::wstring exe = Launcher::ExePath(), cmd = GetCommandLineW();
	STARTUPINFOW si{sizeof(si)};
	PROCESS_INFORMATION pi{};
	if (!CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &pi))
		return false;
	CloseHandle(pi.hThread);
	CloseHandle(pi.hProcess);
	return true;
}

void OfferSelfUpdate() {
	Launcher& l = *g.launcher;
	if (!l.LauncherUpdateAvailable())
		return;
	std::wstring q = L"Downloader version " + Wide(l.LauncherUpdateVersion()) + L" is out (you have " + Wide(LauncherVersion) +
	                 L").\n\nInstall it now? The new exe is verified with the release's sha256, then it replaces this one and "
	                 L"starts again.";
	if (MessageBoxW(g.wnd, q.c_str(), DL_W(DL_PRODUCT_TITLE) L" update", MB_YESNO | MB_ICONQUESTION) == IDYES)
		RunAction(IdSelfUpdate);
}

// ---------------------------------------------------------------- window procedures

LRESULT CALLBACK ButtonProc(HWND h, UINT m, WPARAM w, LPARAM lp, UINT_PTR, DWORD_PTR ref) {
	auto* b = reinterpret_cast<Button*>(ref);
	switch (m) {
	case WM_MOUSEMOVE:
		if (!b->hot) {
			b->hot = true;
			TRACKMOUSEEVENT t{sizeof(t), TME_LEAVE, h, 0};
			TrackMouseEvent(&t);
			InvalidateRect(h, nullptr, FALSE);
		}
		break;
	case WM_MOUSELEAVE:
		b->hot = false;
		InvalidateRect(h, nullptr, FALSE);
		break;
	case WM_SETCURSOR:
		SetCursor(LoadCursorW(nullptr, IDC_HAND));
		return TRUE;
	case WM_ERASEBKGND:
		return 1;
	}
	return DefSubclassProc(h, m, w, lp);
}

void DrawItem(const DRAWITEMSTRUCT& d) {
	Button* b = FindButton(int(d.CtlID));
	if (!b)
		return;
	int w = d.rcItem.right - d.rcItem.left, h = d.rcItem.bottom - d.rcItem.top;
	HDC mem = CreateCompatibleDC(d.hDC);
	HBITMAP bmp = CreateCompatibleBitmap(d.hDC, w, h), old = HBITMAP(SelectObject(mem, bmp));
	POINT at{0, 0};
	MapWindowPoints(d.hwndItem, g.wnd, &at, 1);
	BitBlt(mem, 0, 0, w, h, g.sceneDC, at.x, at.y, SRCCOPY);  // the art behind the button
	{
		gp::Graphics gr(mem);
		Quality(gr);
		gr.ScaleTransform(g.s, g.s);
		gr.TranslateTransform(-at.x / g.s, -at.y / g.s);
		DrawButton(gr, *b, (d.itemState & ODS_SELECTED) != 0, (d.itemState & ODS_DISABLED) != 0,
		           (d.itemState & ODS_FOCUS) && !(d.itemState & ODS_NOFOCUSRECT));
	}
	BitBlt(d.hDC, 0, 0, w, h, mem, 0, 0, SRCCOPY);
	SelectObject(mem, old);
	DeleteObject(bmp);
	DeleteDC(mem);
}

void Paint(HWND h) {
	PAINTSTRUCT ps;
	HDC dc = BeginPaint(h, &ps);
	const RECT& u = ps.rcPaint;
	int uw = u.right - u.left, uh = u.bottom - u.top;
	BitBlt(g.backDC, u.left, u.top, uw, uh, g.sceneDC, u.left, u.top, SRCCOPY);
	{
		gp::Graphics gr(g.backDC);
		gr.SetClip(gp::Rect(u.left, u.top, uw, uh));
		DrawDynamic(gr);
	}
	BitBlt(dc, u.left, u.top, uw, uh, g.backDC, u.left, u.top, SRCCOPY);
	EndPaint(h, &ps);
}

void Tick() {
	ULONGLONG now = GetTickCount64();
	if (g.art->logo.size() > 1 && now - g.frameAt >= g.art->delays[g.frame]) {
		g.frameAt = now;
		g.frame = (g.frame + 1) % g.art->logo.size();
		RECT r{Px(LogoRect.X), Px(LogoRect.Y), Px(LogoRect.X + LogoRect.Width), Px(LogoRect.Y + LogoRect.Height)};
		InvalidateRect(g.wnd, &r, FALSE);
	}
	if (g.fraction > -2 && g.fraction < 0) {
		++g.marquee;
		RECT r{Px(BarRect.X) - 1, Px(BarRect.Y) - 1, Px(BarRect.X + BarRect.Width) + 2, Px(BarRect.Y + BarRect.Height) + 2};
		InvalidateRect(g.wnd, &r, FALSE);
	}
}

LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM lp) {
	switch (m) {
	case WM_COMMAND:
		switch (LOWORD(w)) {
		case IdInstall: case IdRepair: case IdUninstall: case IdDefaultMod: case IdMovePlugins: RunAction(LOWORD(w)); return 0;
		case IdMods: {  // opened, never created: the downloader writes only mods\the mod
			std::wstring d = g.launcher->ModDir();
			if (!d.empty() && !DirExists(d))
				d = ParentDir(d);
			if (!d.empty() && !DirExists(d))
				d = g.launcher->D2RDir();
			if (!d.empty())
				ShellExecuteW(h, L"open", d.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
			return 0;
		}
		case IdLoader: ShellExecuteW(h, L"open", Wide(LoaderSite).c_str(), nullptr, nullptr, SW_SHOWNORMAL); return 0;
		case IdIssues: ShellExecuteW(h, L"open", Wide(IssuesUrl).c_str(), nullptr, nullptr, SW_SHOWNORMAL); return 0;
		case IdChoose: if (!g.busy) ChooseSource(); return 0;
		case IdCancel: g.launcher->cancel = true; return 0;
		}
		break;
	case WM_DRAWITEM:
		DrawItem(*reinterpret_cast<const DRAWITEMSTRUCT*>(lp));
		return TRUE;
	case WM_ERASEBKGND:
		return 1;
	case WM_PAINT:
		Paint(h);
		return 0;
	case WM_TIMER:
		if (w == AnimTimer)
			Tick();
		return 0;
	case WmProgress: case WmDone: {
		std::unique_ptr<ProgressMsg> msg(reinterpret_cast<ProgressMsg*>(lp));
		g.line = msg->text;
		g.lineError = m == WmDone && w != 1;
		g.fraction = msg->fraction;
		if (m == WmDone) {
			g.fraction = -2;
			SetBusy(false);
			RefreshStatus();
			if (!g.snapshot.empty()) {  // after the start-up check: draw, save, close
				Snapshot(g.snapshot);
				DestroyWindow(h);
				return 0;
			}
			if (msg->action == IdSelfUpdate && w == 1) {
				if (Restart()) {
					DestroyWindow(h);
					return 0;
				}
				MessageBoxW(h, L"Update finished. Please open the downloader again.", DL_W(DL_PRODUCT_TITLE), MB_ICONINFORMATION);
			}
			if (w == 0 && msg->action == IdInstall && g.launcher->ForeignModFolder()) {
				AskReplace(msg->text);
				return 0;
			}
			if (w == 0)
				MessageBoxW(h, Wide(msg->text).c_str(), DL_W(DL_PRODUCT_TITLE), MB_ICONWARNING);
			if (msg->action == 0) {
				InvalidatePanel();
				OfferSelfUpdate();
				return 0;
			}
		}
		InvalidatePanel();
		return 0;
	}
	case WM_CLOSE:
		if (g.busy && MessageBoxW(h, L"Still installing. Cancel and close? The next run picks up where this one stopped.", DL_W(DL_PRODUCT_TITLE),
		                          MB_YESNO | MB_ICONQUESTION) != IDYES)
			return 0;
		g.launcher->cancel = true;
		if (g.worker.joinable())
			g.worker.join();
		DestroyWindow(h);
		return 0;
	case WM_DESTROY:
		KillTimer(h, AnimTimer);
		PostQuitMessage(0);
		return 0;
	}
	return DefWindowProcW(h, m, w, lp);
}

// Canvas scale: the monitor's DPI, made smaller if the window would not fit the work area.
float ChooseScale(DWORD style) {
	float s = float(GetDpiForSystem()) / 96.f;
	RECT work{0, 0, 1280, 720};
	SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
	RECT frame{0, 0, 0, 0};
	AdjustWindowRectEx(&frame, style, FALSE, 0);
	float fw = float(work.right - work.left - (frame.right - frame.left)) / CanvasW;
	float fh = float(work.bottom - work.top - (frame.bottom - frame.top)) / CanvasH;
	return std::max(0.5f, std::min({s, fw, fh}));
}

void MakeLayers() {
	HDC screen = GetDC(nullptr);
	g.sceneDC = CreateCompatibleDC(screen);
	g.backDC = CreateCompatibleDC(screen);
	g.sceneBmp = CreateCompatibleBitmap(screen, g.w, g.h);
	g.backBmp = CreateCompatibleBitmap(screen, g.w, g.h);
	ReleaseDC(nullptr, screen);
	g.sceneOld = HBITMAP(SelectObject(g.sceneDC, g.sceneBmp));
	g.backOld = HBITMAP(SelectObject(g.backDC, g.backBmp));
	gp::Graphics gr(g.sceneDC);
	Quality(gr);
	DrawScene(gr);
}

void FreeLayers() {
	SelectObject(g.sceneDC, g.sceneOld);
	SelectObject(g.backDC, g.backOld);
	DeleteObject(g.sceneBmp);
	DeleteObject(g.backBmp);
	DeleteDC(g.sceneDC);
	DeleteDC(g.backDC);
}

}  // namespace

int RunWindow(HINSTANCE inst, const Options& opt, const std::wstring& snapshot) {
	g.inst = inst;
	g.opt = opt;
	g.snapshot = snapshot;
	CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
	INITCOMMONCONTROLSEX icc{sizeof(icc), ICC_STANDARD_CLASSES | ICC_WIN95_CLASSES};
	InitCommonControlsEx(&icc);
	gp::GdiplusStartupInput gsi;
	ULONG_PTR gdiplus = 0;
	if (gp::GdiplusStartup(&gdiplus, &gsi, nullptr) != gp::Ok) {
		MessageBoxW(nullptr, L"GDI+ failed to start.", DL_W(DL_PRODUCT_TITLE), MB_ICONERROR);
		return 1;
	}

	WNDCLASSEXW wc{sizeof(wc)};
	wc.lpfnWndProc = WndProc;
	wc.hInstance = inst;
	wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
	wc.hIcon = LoadIconW(inst, MAKEINTRESOURCEW(1));
	wc.lpszClassName = L"ModDownloaderWindow";
	RegisterClassExW(&wc);

	const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN;
	g.s = ChooseScale(style);
	g.w = Px(CanvasW);
	g.h = Px(CanvasH);
	RECT r{0, 0, g.w, g.h};
	AdjustWindowRectEx(&r, style, FALSE, 0);
	std::wstring title = Wide(ProductTitle) + L" " + Wide(LauncherVersion);
	g.wnd = CreateWindowExW(0, wc.lpszClassName, title.c_str(), style, CW_USEDEFAULT, CW_USEDEFAULT, r.right - r.left, r.bottom - r.top,
	                        nullptr, nullptr, inst, nullptr);
	BOOL dark = TRUE;  // a dark title bar to match the art (Windows 10 2004+; ignored elsewhere)
	DwmSetWindowAttribute(g.wnd, 20 /* DWMWA_USE_IMMERSIVE_DARK_MODE */, &dark, sizeof(dark));

	g.art = std::make_unique<Art>();
	LoadArt();
	MakeLayers();
	for (auto& b : buttons) {
		DWORD bs = WS_CHILD | WS_TABSTOP | BS_OWNERDRAW | (b.id == IdCancel || b.id == IdLoader || b.id == IdDefaultMod || b.id == IdMovePlugins ? 0 : WS_VISIBLE);
		b.h = CreateWindowExW(0, L"BUTTON", b.label.c_str(), bs, Px(b.r.X), Px(b.r.Y), Px(b.r.Width), Px(b.r.Height), g.wnd,
		                      HMENU(INT_PTR(b.id)), inst, nullptr);
		SetWindowSubclass(b.h, ButtonProc, 0, reinterpret_cast<DWORD_PTR>(&b));
	}
	g.tip = CreateWindowExW(WS_EX_TOPMOST, TOOLTIPS_CLASSW, nullptr, WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX, 0, 0, 0, 0, g.wnd, nullptr,
	                        inst, nullptr);
	TTTOOLINFOW ti{sizeof(ti)};
	ti.uFlags = TTF_IDISHWND | TTF_SUBCLASS;
	ti.hwnd = g.wnd;
	ti.uId = UINT_PTR(FindButton(IdIssues)->h);
	ti.lpszText = const_cast<wchar_t*>(L"Open the issue tracker in your browser");
	SendMessageW(g.tip, TTM_ADDTOOLW, 0, LPARAM(&ti));
	ti.uId = UINT_PTR(FindButton(IdDefaultMod)->h);
	ti.lpszText = const_cast<wchar_t*>(L"Set default_mod in D2RLoader's settings to " DL_W(DL_MOD_FOLDER) L" (you confirm first)");
	SendMessageW(g.tip, TTM_ADDTOOLW, 0, LPARAM(&ti));
	ti.uId = UINT_PTR(FindButton(IdMovePlugins)->h);
	ti.lpszText = const_cast<wchar_t*>(L"Move stray copies of the " DL_W(DL_MOD_NAME) L" plugin to a backup folder (you confirm first)");
	SendMessageW(g.tip, TTM_ADDTOOLW, 0, LPARAM(&ti));

	g.launcher = std::make_unique<Launcher>(g.opt);
	g.launcher->onProgress = [](const Progress& p) {
		PostMessageW(g.wnd, WmProgress, 0, reinterpret_cast<LPARAM>(new ProgressMsg{p.text, p.fraction}));
	};
	RefreshStatus();
	SetBusy(true);
	g.fraction = -1;
	SetTimer(g.wnd, AnimTimer, 20, nullptr);
	if (g.snapshot.empty())
		ShowWindow(g.wnd, SW_SHOWNORMAL);
	g.worker = std::thread([] {  // the release check at start, so the status shows "update available"
		std::string err;
		bool ok = g.launcher->CheckFeed(err);
		PostMessageW(g.wnd, WmDone, ok ? 1 : 2, reinterpret_cast<LPARAM>(new ProgressMsg{ok ? "Ready." : err, -2}));
	});
	MSG msg;
	while (GetMessageW(&msg, nullptr, 0, 0)) {
		if (!IsDialogMessageW(g.wnd, &msg)) {
			TranslateMessage(&msg);
			DispatchMessageW(&msg);
		}
	}
	if (g.worker.joinable())
		g.worker.join();
	g.launcher.reset();
	FreeLayers();
	g.art.reset();  // GDI+ objects go before GdiplusShutdown
	gp::GdiplusShutdown(gdiplus);
	return 0;
}

}  // namespace dl
