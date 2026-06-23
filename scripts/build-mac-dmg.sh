#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m venv .venv-mac-build
source .venv-mac-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt pyinstaller

pyinstaller \
  --clean \
  --onefile \
  --name linkedin-scan-api \
  --distpath frontend/electron/backend \
  --workpath build/pyinstaller \
  --specpath build/pyinstaller \
  backend/desktop_server.py

cd frontend
npm install
npm run dist:mac
