#!/usr/bin/env bash
# One-time EC2 setup: FYP scrapers + Playwright (Ubuntu or Amazon Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

install_system_deps() {
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi

  echo "==> OS: ${NAME:-unknown} ${VERSION_ID:-}"

  if command -v apt-get >/dev/null 2>&1; then
    echo "==> Installing packages (apt)"
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
      python3 python3-pip python3-venv git \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
      libpango-1.0-0 libcairo2 libasound2 fonts-liberation
    return
  fi

  if command -v dnf >/dev/null 2>&1; then
    echo "==> Installing packages (dnf) — Amazon Linux / Fedora"
    sudo dnf install -y \
      python3 python3-pip git \
      nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon \
      libXcomposite libXdamage libXfixes libXrandr mesa-libgbm \
      pango cairo alsa-lib liberation-fonts \
      libX11 libXext libXcursor libXi libXtst libXScrnSaver \
      gtk3 mesa-libEGL vulkan-loader
    return
  fi

  if command -v yum >/dev/null 2>&1; then
    echo "==> Installing packages (yum) — Amazon Linux 2"
    sudo yum install -y \
      python3 python3-pip git \
      nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon \
      libXcomposite libXdamage libXfixes libXrandr mesa-libgbm \
      pango cairo alsa-lib liberation-fonts
    return
  fi

  echo "ERROR: Unknown package manager. Install Playwright deps manually."
  exit 1
}

install_system_deps

echo "==> Python venv"
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Playwright Chromium"
playwright install chromium

echo "==> Playwright system dependencies"
if command -v dnf >/dev/null 2>&1; then
  bash "$ROOT/scripts/install_playwright_deps_amzn.sh"
elif command -v yum >/dev/null 2>&1; then
  bash "$ROOT/scripts/install_playwright_deps_amzn.sh" || true
else
  playwright install-deps chromium 2>/dev/null || sudo playwright install-deps chromium || true
fi

echo "==> Verify browser launch"
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--no-sandbox'])
    b.close()
print('Chromium launch OK')
"

if [ ! -f .env ]; then
  cat > .env <<'EOF'
MONGODB_USERNAME=your_user
MONGODB_PASSWORD=your_pass
EOF
  echo "Created .env — edit with MongoDB credentials."
fi

chmod +x scripts/run_urdupoint_search.sh scripts/run_all_scrapers_ec2.sh 2>/dev/null || true

echo ""
echo "Setup done. Run:"
echo "  source venv/bin/activate"
echo "  ./scripts/run_urdupoint_search.sh"
