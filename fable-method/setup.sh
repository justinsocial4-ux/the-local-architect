#!/usr/bin/env bash
#
# Fable Method — one-command setup for the MCP server ("strict enforcement" mode).
#
# What this does:
#   1. Creates an isolated Python environment (.venv) — this sidesteps the
#      "externally-managed-environment" pip error you may hit on macOS.
#   2. Installs the engine + the `mcp` dependency into it.
#   3. Runs a quick self-test.
#   4. Prints the exact config line to paste into your AI app, with the path
#      already filled in — no hand-editing.
#
# Requires Python 3.10+. macOS / Linux.
# (Windows: run the two commands under "MANUAL" below in PowerShell.)

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Setting up the Fable Method MCP server in: $ROOT"

# 1. isolated environment
echo "==> Creating virtual environment (.venv) ..."
python3 -m venv .venv

# 2. install engine + mcp into it
echo "==> Installing (this can take a minute) ..."
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -e "./enforcer[mcp]"

# 3. self-test (lightweight; needs no extra dev tools)
echo "==> Running self-test ..."
if ./.venv/bin/python -c "import tempfile, fable_method.mcp_server; from fable_method.engine import Engine; assert Engine(store_dir=tempfile.mkdtemp()).create_session('setup self-test', rigor='low')['current_stage'] == 'frame'" >/dev/null 2>&1; then
  echo "    Self-test PASSED — engine works and the MCP server imports."
else
  echo "    WARNING: self-test did not pass cleanly — check the output above."
fi

SERVER="$ROOT/.venv/bin/fable-method-server"

cat <<BANNER

============================================================
 DONE.  Add this server to your AI app's MCP config.
============================================================

Paste this INSIDE the top-level "mcpServers": { ... } object:

  "fable-method": {
    "command": "$SERVER"
  }

------------------------------------------------------------
For Claude Desktop, the config file lives at:

  macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
  Windows: %APPDATA%\\Claude\\claude_desktop_config.json

If the file already has other servers, just add the
"fable-method" block alongside them (mind the commas).
If it's empty, the whole file should look like:

  {
    "mcpServers": {
      "fable-method": {
        "command": "$SERVER"
      }
    }
  }

Then FULLY QUIT and reopen the app. The fable-method tools
(begin_task, submit_stage, finalize, ...) will be available.
============================================================

MANUAL (Windows / if you'd rather do it by hand):
  python3 -m venv .venv
  .venv/bin/python -m pip install -e "./enforcer[mcp]"
  # then use  .venv/bin/fable-method-server  as the command above
BANNER
