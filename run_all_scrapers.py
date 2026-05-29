#!/usr/bin/env python3
"""
Run all news scrapers (same list as .github/workflows/cron-jobs.yaml).

EC2 / Linux:
  pip install -r requirements.txt
  export MONGODB_USERNAME=...
  export MONGODB_PASSWORD=...
  # or: export MONGODB_URI=...
  python3 run_all_scrapers.py

Windows:
  python run_all_scrapers.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
SCRAPY_DIR = ROOT / "FYP_Scraper"

# Same spiders as cron-jobs.yaml (name, extra scrapy args)
SPIDERS: list[tuple[str, list[str]]] = [
    ("city42", []),
    ("daily_Pakistan", []),
    ("urdupoint_multi_category", ["-a", "selected_category=murder"]),
    ("urdupoint_multi_category", ["-a", "selected_category=thief"]),
    ("urdupoint_multi_category", ["-a", "selected_category=robbery"]),
    ("urdupoint_multi_category", ["-a", "selected_category=terrorism"]),
    ("urdupoint_multi_category", ["-a", "selected_category=kidnapping"]),
    ("urdupoint_multi_category", ["-a", "selected_category=rape"]),
    ("urdupoint_multi_category", ["-a", "selected_category=suicide"]),
    ("nawaiwaqt", []),
    ("24_news", []),
    ("dunya_news", []),
]


def load_env() -> None:
    if load_dotenv is None:
        return
    for path in (ROOT / ".env", SCRAPY_DIR / ".env"):
        if path.is_file():
            load_dotenv(path)


def run_spider(name: str, extra_args: list[str]) -> bool:
    cmd = [sys.executable, "-m", "scrapy", "crawl", name, *extra_args]
    label = name if not extra_args else f"{name} {' '.join(extra_args)}"
    print(f"\n{'=' * 60}\n>>> {label}\n{'=' * 60}")
    result = subprocess.run(cmd, cwd=SCRAPY_DIR)
    if result.returncode != 0:
        print(f"FAILED: {label} (exit {result.returncode})")
        return False
    print(f"OK: {label}")
    return True


def main() -> None:
    load_env()
    if not SCRAPY_DIR.is_dir():
        print(f"Error: FYP_Scraper not found at {SCRAPY_DIR}", file=sys.stderr)
        sys.exit(1)

    failed: list[str] = []
    for name, extra in SPIDERS:
        if not run_spider(name, extra):
            label = name if not extra else f"{name} {extra[-1]}"
            failed.append(label)

    print(f"\nDone. {len(SPIDERS) - len(failed)}/{len(SPIDERS)} succeeded.")
    if failed:
        print("Failed:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
