#!/usr/bin/env python3
"""Claude Code hook handler - entry point for hook events."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from lib import hook
hook.handle_hook_event()
