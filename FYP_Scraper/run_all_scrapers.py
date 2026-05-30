#!/usr/bin/env python3
"""Launcher when you run from the FYP_Scraper folder."""
import runpy
import sys
from pathlib import Path

root_script = Path(__file__).resolve().parent.parent / "run_all_scrapers.py"
if not root_script.is_file():
    print(f"Error: not found: {root_script}", file=sys.stderr)
    sys.exit(1)
runpy.run_path(str(root_script), run_name="__main__")
