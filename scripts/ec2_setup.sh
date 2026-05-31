#!/usr/bin/env bash
# One-time setup on Ubuntu EC2 for FYP scrapers + Playwright (urdupoint_search).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REPO_DIR="$ROOT"

echo "==> Installing system packages (Ubuntu/Debian)"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3 python3-pip python3-venv git \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libasound2t64 libasound2 \
  fonts-liberation fonts-noto-core 2>/dev/null || \
sudo apt-get install -y -qq \
  python3 python3-pip python3-venv git \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libasound2 fonts-liberation

if [ ! -d "$REPO_DIR" ]; then
  echo "Clone your repo first, e.g.:"
  echo "  git clone <your-repo-url> $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

echo "==> Python venv"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Playwright Chromium (required for urdupoint_search)"
playwright install chromium
playwright install-deps chromium 2>/dev/null || true

echo "==> .env"
if [ ! -f .env ]; then
  cat > .env <<'EOF'
MONGODB_USERNAME=your_user
MONGODB_PASSWORD=your_pass
# Or use full URI:
# MONGODB_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true&w=majority
EOF
  echo "Created .env — edit with your MongoDB credentials."
else
  echo ".env already exists."
fi

chmod +x scripts/run_urdupoint_search.sh scripts/run_all_scrapers_ec2.sh 2>/dev/null || true

echo ""
echo "Setup done. Test:"
echo "  cd $REPO_DIR && source venv/bin/activate"
echo "  ./scripts/run_urdupoint_search.sh"
