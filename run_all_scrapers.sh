#!/usr/bin/env bash
# Run all scrapers (same as cron-jobs.yaml). For EC2/Linux.
set -euo pipefail

cd "$(dirname "$0")/FYP_Scraper"

if [ -f ../.env ]; then
  set -a
  # shellcheck disable=SC1091
  source ../.env
  set +a
fi

run() {
  echo ""
  echo "============================================================"
  echo ">>> $*"
  echo "============================================================"
  scrapy crawl "$@" || echo "FAILED: $*"
}

run city42
run daily_Pakistan
run urdupoint_multi_category -a selected_category=murder
run urdupoint_multi_category -a selected_category=thief
run urdupoint_multi_category -a selected_category=robbery
run urdupoint_multi_category -a selected_category=terrorism
run urdupoint_multi_category -a selected_category=kidnapping
run urdupoint_multi_category -a selected_category=rape
run urdupoint_multi_category -a selected_category=suicide
run nawaiwaqt
run 24_news
run dunya_news

echo ""
echo "All scrapers finished."
