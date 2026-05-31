#!/usr/bin/env bash
# Run UrduPoint search spider — all Google CSE pages + every article (EC2).
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

# Search keyword (Urdu)
QUERY="${QUERY:-زیادتی}"

# Google CSE pagination: "all" = every result page (default)
# Or set a number, e.g. MAX_PAGES=10
MAX_PAGES="${MAX_PAGES:-all}"

cd FYP_Scraper
echo "Starting urdupoint_search"
echo "  query=$QUERY"
echo "  max_pages=$MAX_PAGES (all = scrape every search result page)"
echo "  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

scrapy crawl urdupoint_search \
  -a "query=${QUERY}" \
  -a "max_pages=${MAX_PAGES}" \
  -s PLAYWRIGHT_HEADLESS=true \
  -s LOG_LEVEL=INFO

echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
