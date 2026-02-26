#!/usr/bin/env python3
"""Backward-compatible entry for page capture.

Use `frontend_usability_smoke.py` for Wave7 checks.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    script = Path(__file__).with_name("frontend_usability_smoke.py")
    # Preserve CLI arguments.
    sys.argv[0] = str(script)
    runpy.run_path(str(script), run_name="__main__")
