#!/usr/bin/env bash
# The live tech-intel listener (python -m techintel watch): queues new domains from the
# certificate stream; `techintel quick` (profile "frequent") adds and qualifies them.
#
#   ./watch.sh             run in the foreground (Ctrl+C to stop)
#   ./watch.sh --ensure    start it in the background unless it's already running (used by cron)
#   ./watch.sh --stop      stop a background listener
#   ./watch.sh --status    is it running, and its last batch
#
# Normally you don't call this directly: use  ./schedule.sh watch on|off|status

set -uo pipefail
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
LOG_DIR="$PROJECT_DIR/logs"
PIDFILE="$LOG_DIR/watch.pid"
# Same check as run_all.sh: under launchd, macOS may block the external drive (see ./schedule.sh status).
if ! { mkdir -p "$LOG_DIR" && touch "$LOG_DIR/.access-check"; } 2>/dev/null; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') Can't write to $LOG_DIR: Operation not permitted." >&2
  echo "  On a Mac, give /bin/bash Full Disk Access (System Settings → Privacy & Security), then run ./schedule.sh doctor." >&2
  exit 1
fi
# shellcheck disable=SC1091
source ./env.sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  RUN=("$PROJECT_DIR/.venv/bin/python" -m techintel watch)
else
  RUN=("$(command -v uv)" run --frozen python -m techintel watch)
fi

running_pid() { local p; p="$(cat "$PIDFILE" 2>/dev/null || true)"; if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo "$p"; fi; }

rotate_log() {   # keep logs/watch.log under ~5 MB
  local f="$LOG_DIR/watch.log"
  if [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 5000000 ]; then mv -f "$f" "$f.1"; fi
}

case "${1:-}" in
  --ensure)
    if [ -n "$(running_pid)" ]; then exit 0; fi
    rotate_log
    nohup "${RUN[@]}" >> "$LOG_DIR/watch.log" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Listener started (pid $!). Log: $LOG_DIR/watch.log"
    ;;
  --stop)
    p="$(running_pid)"
    if [ -n "$p" ]; then kill "$p" && echo "Listener stopped (pid $p)."; else echo "Listener is not running."; fi
    rm -f "$PIDFILE"
    ;;
  --status)
    p="$(running_pid)"
    if [ -n "$p" ]; then echo "Listener: running (pid $p)"; else echo "Listener: not running"; fi
    STATUS_FILE="${TECHINTEL_DATA_DIR:-$PROJECT_DIR/data/techintel}/stream/status.json"
    if [ -f "$STATUS_FILE" ]; then
      grep -E '"(started_at|last_batch_at|batches|certificates|queued|errors|last_error)"' "$STATUS_FILE" \
        | sed -E 's/^ *"([a-z_]+)": */  \1: /; s/[",]//g'      # (grep -E / sed -E: same on macOS and Linux)
    fi
    PENDING="${TECHINTEL_DATA_DIR:-$PROJECT_DIR/data/techintel}/stream/pending.jsonl"
    if [ -f "$PENDING" ]; then echo "  waiting for the next quick run: $(wc -l < "$PENDING" | tr -d ' ') domain(s)"; fi
    ;;
  ""|--foreground)
    rotate_log
    echo $$ > "$PIDFILE"
    # launchd's own log is on the Mac's drive (see schedule.sh), so the listener writes logs/watch.log itself
    if [ -t 1 ]; then exec "${RUN[@]}"; else exec "${RUN[@]}" >> "$LOG_DIR/watch.log" 2>&1; fi
    ;;
  -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "Unknown option $1 (try --help)"; exit 2 ;;
esac
