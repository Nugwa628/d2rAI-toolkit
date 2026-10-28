#include "log.h"

#include <cstdarg>
#include <cstdio>

namespace plog {

namespace {

const D2RL::PluginContext* g_context = nullptr;
const char* g_prefix = "plugin";

enum class Level { Info, Warn, Error };

void Write(Level level, const char* fmt, va_list args) noexcept {
	if (g_context == nullptr || fmt == nullptr) {
		return;
	}
	char body[900];
	std::vsnprintf(body, sizeof(body), fmt, args);
	char line[1024];
	std::snprintf(line, sizeof(line), "%s: %s", g_prefix, body);
	switch (level) {
	case Level::Info: g_context->LogInfo(line); break;
	case Level::Warn: g_context->LogWarn(line); break;
	case Level::Error: g_context->LogError(line); break;
	}
}

}  // namespace

void Init(const D2RL::PluginContext* context, const char* prefix) noexcept {
	g_context = context;
	if (prefix != nullptr) {
		g_prefix = prefix;
	}
}

void Info(const char* fmt, ...) noexcept {
	va_list args;
	va_start(args, fmt);
	Write(Level::Info, fmt, args);
	va_end(args);
}

void Warn(const char* fmt, ...) noexcept {
	va_list args;
	va_start(args, fmt);
	Write(Level::Warn, fmt, args);
	va_end(args);
}

void Error(const char* fmt, ...) noexcept {
	va_list args;
	va_start(args, fmt);
	Write(Level::Error, fmt, args);
	va_end(args);
}

auto Sample(std::atomic<uint32_t>& counter) noexcept -> bool {
	const uint32_t n = ++counter;
	return n == 1 || n == 100 || n == 10000 || n == 1000000;
}

}  // namespace plog
