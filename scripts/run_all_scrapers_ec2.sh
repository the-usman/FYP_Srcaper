#!/usr/bin/env bash
# Run all scrapers from run_all_scrapers.py on EC2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

export PLAYWRIGHT_HEADLESS=true

echo "Starting all scrapers at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 run_all_scrapers.py
echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
