#!/usr/bin/env bash
# Playwright browser dependencies for Amazon Linux 2023 (install-deps uses apt-get and fails).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/venv/bin/python3"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

if ! command -v dnf >/dev/null 2>&1; then
  echo "This script is for Amazon Linux (dnf). On Ubuntu use: playwright install-deps chromium"
  exit 1
fi

echo "Installing Chromium system libraries via dnf..."
sudo dnf install -y \
  atk at-spi2-atk cups-libs libdrm libxkbcommon \
  libXcomposite libXdamage libXfixes libXrandr mesa-libgbm \
  pango cairo alsa-lib nss nspr gtk3 \
  libX11 libXext libXcursor libXi libXtst libXScrnSaver \
  mesa-libEGL vulkan-loader liberation-fonts

echo ""
echo "Testing Chromium launch ($PY)..."
"$PY" -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    b.close()
print('Chromium OK — run: cd FYP_Scraper && scrapy crawl urdupoint_search -s PLAYWRIGHT_HEADLESS=true')
"
