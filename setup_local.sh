#!/usr/bin/env bash
# One-time setup that keeps everything inside this project folder:
#   .venv/    Python packages (crawl4ai, psycopg, ...)
#   .local/   uv cache, Python (if downloaded), Chromium browser, crawl4ai cache
#   data/     your databases (DuckDB) and collected data
#
#   bash setup_local.sh
set -euo pipefail
cd "$(dirname "$0")"
source ./env.sh

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "==> Installing Python packages into $PROJECT_DIR/.venv"
uv sync --frozen

echo "==> Installing the browser into $PLAYWRIGHT_BROWSERS_PATH"
uv run crawl4ai-setup

echo "==> Creating the databases in $PROJECT_DIR/data"
uv run python -m warehouse init

echo
echo "Done. Everything lives in: $PROJECT_DIR"
du -sh .venv .local data 2>/dev/null || true
echo
echo "In each new Terminal window, run:  source env.sh"
