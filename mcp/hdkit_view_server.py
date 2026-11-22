#!/usr/bin/env python3
"""Start hdkit.view's MCP server (stdio) from any folder: the thin entry an MCP client points at (mcp/README.md).

All tools and their logic live in hdkit/view/mcp_server.py; this file only puts the repository root on sys.path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.view import mcp_server  # noqa: E402

if __name__ == "__main__":
    sys.exit(mcp_server.main())
