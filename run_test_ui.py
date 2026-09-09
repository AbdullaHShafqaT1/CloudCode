"""
run_test_ui.py — One-click launcher for the LegacyBridge + NodeInsight Test UI.

Usage:
    python run_test_ui.py          # starts on http://localhost:7490
    python run_test_ui.py 8080     # custom port

This script simply delegates to test_ui_server.py.
Run from the CloudCode/ directory (project root).
"""

import subprocess
import sys
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_SERVER = _THIS / "test_ui_server.py"

if not _SERVER.exists():
    print(f"ERROR: test_ui_server.py not found at {_SERVER}")
    sys.exit(1)

args = [sys.executable, str(_SERVER)] + sys.argv[1:]
try:
    subprocess.run(args)
except KeyboardInterrupt:
    pass
