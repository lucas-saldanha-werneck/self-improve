#!/usr/bin/env python3
"""SessionStart hook: create ~/.config/self-improve/config.json (si_config.CONFIG_FILE) from
config.example.json the first time, since installing a plugin runs no hook. Nothing is created while the
old ~/.claude/self-improve/config.json is the one in use.

Never overwrites an existing file, prints nothing, and always exits 0 so it never blocks a session."""
import os
import sys

if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import si_config
        si_config.seed_config()
    except BaseException:
        pass
    sys.exit(0)
