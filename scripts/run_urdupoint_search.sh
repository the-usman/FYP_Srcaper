#!/usr/bin/env bash
# Run UrduPoint search spider on EC2 (headless Playwright).
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

QUERY="${QUERY:-زیادتی}"
MAX_PAGES="${MAX_PAGES:-5}"

cd FYP_Scraper
echo "Starting urdupoint_search query=$QUERY max_pages=$MAX_PAGES at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
scrapy crawl urdupoint_search \
  -a "query=${QUERY}" \
  -a "max_pages=${MAX_PAGES}" \
  -s PLAYWRIGHT_HEADLESS=true \
  -s LOG_LEVEL=INFO

echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
