# Keep every tool's files inside this project folder (e.g. on an external drive).
#
#   source env.sh          (run once per Terminal window, from the project folder)
#
# Or add this line to ~/.zshrc to do it automatically when the drive is connected:
#   [ -f /Volumes/MAC-DISK2/crawl4ai/env.sh ] && source /Volumes/MAC-DISK2/crawl4ai/env.sh

_env_src="${BASH_SOURCE[0]:-$0}"
PROJECT_DIR="$(cd "$(dirname "$_env_src")" && pwd)"
export PROJECT_DIR
export PROJECT_LOCAL_DIR="$PROJECT_DIR/.local"

export UV_CACHE_DIR="$PROJECT_LOCAL_DIR/uv-cache"                 # downloaded packages
export UV_PYTHON_INSTALL_DIR="$PROJECT_LOCAL_DIR/python"          # Python, if uv needs to download one
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_LOCAL_DIR/ms-playwright" # Chromium for crawl4ai
export CRAWL4_AI_BASE_DIRECTORY="$PROJECT_LOCAL_DIR/crawl4ai"      # crawl4ai cache and settings
export TLDEXTRACT_CACHE="$PROJECT_LOCAL_DIR/tldextract"
export CRAWL4AI_NO_CLOUD_NOTICE=1

mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$PLAYWRIGHT_BROWSERS_PATH" "$CRAWL4_AI_BASE_DIRECTORY" "$TLDEXTRACT_CACHE"
unset _env_src
