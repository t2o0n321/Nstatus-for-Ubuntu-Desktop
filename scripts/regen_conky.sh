#!/bin/bash
# Immediately regenerate conky_data.txt from the latest state.json.
# Called by the Conky Lua mouse hook right after toggling simple_mode.
"$HOME/.local/share/nstatus/venv/bin/python3" - <<'PYTHON'
import json, sys
from pathlib import Path
home = Path.home()
sys.path.insert(0, str(home / ".config/nstatus"))
from src.storage.state_writer import write_conky_data
try:
    state = json.loads((home / ".local/share/nstatus/state.json").read_text())
    write_conky_data(home / ".local/share/nstatus/conky_data.txt", state)
except Exception as e:
    sys.stderr.write(f"regen_conky error: {e}\n")
PYTHON
