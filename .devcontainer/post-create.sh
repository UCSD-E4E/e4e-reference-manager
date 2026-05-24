#!/usr/bin/env bash
# Runs once after the dev container is created. Installs project toolchains so the
# editor has IntelliSense for both the Python API and the TypeScript frontend.
set -euo pipefail

# Avoid git "dubious ownership" warnings on the bind-mounted workspace
git config --global --add safe.directory "$(pwd)" || true

echo "==> Installing uv"
python -m pip install --quiet --upgrade uv

echo "==> Syncing API dependencies (creates api/.venv)"
(cd api && uv sync)

echo "==> Installing web dependencies"
(cd web && npm install --no-audit --no-fund)

echo "==> Dev container ready."
echo "    The app is already running: web -> http://localhost:5173, API -> http://localhost:8000/docs"
