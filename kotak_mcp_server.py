"""Entry point for the Kotak Neo MCP server.

Launched by the VSCode Claude Code extension via .mcp.json.
Sets up sys.path to reach venv packages and project modules, then starts FastMCP.
"""
import sys
import os

os.environ["FASTMCP_CHECK_FOR_UPDATES"] = "off"
os.environ["FASTMCP_SHOW_SERVER_BANNER"] = "false"

_VENV_SITE = r"C:\Users\Harsh Shah\OneDrive\Desktop\Claude-Coding\india-auto-trader\.venv\Lib\site-packages"
_PROJECT   = r"C:\Users\Harsh Shah\OneDrive\Desktop\Claude-Coding\india-auto-trader"
for _p in (_VENV_SITE, _PROJECT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcp_servers.kotak_neo import mcp

mcp.run()
