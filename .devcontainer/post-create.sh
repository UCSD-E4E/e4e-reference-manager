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
# The `web` compose service mounts a named volume at web/node_modules, so Docker
# creates that mountpoint on the host bind dir as root. On a fresh checkout that
# leaves an empty root-owned dir here, which breaks `npm install` (run as vscode)
# with EACCES. Reclaim it before installing.
if [ -d web/node_modules ] && [ ! -O web/node_modules ]; then
  echo "    Reclaiming root-owned web/node_modules mountpoint"
  sudo chown "$(id -u):$(id -g)" web/node_modules
fi
(cd web && npm install --no-audit --no-fund)

echo "==> Dev container ready."
echo "    The app is already running: web -> http://localhost:5173, API -> http://localhost:8000/docs"
