#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r backend/requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
python -m uvicorn backend.api:app --host 0.0.0.0 --port 8000
