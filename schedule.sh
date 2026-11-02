#!/usr/bin/env bash
# Run ./run_all.sh automatically.
#
#   ./schedule.sh every 1h              every hour        (also: 30m, 2h, 10h, ...)
#   ./schedule.sh daily 07:30 19:30     at fixed times every day
#   ./schedule.sh status                is it on, when did it last run
#   ./schedule.sh off                   stop the automatic runs
#   ./schedule.sh run-now               start one scheduled run right away (in the background)
#
# macOS uses launchd (a LaunchAgent: runs while you're logged in; a run missed while the Mac
# slept happens when it wakes). Linux uses your crontab. Add --dry-run to see what would be set up.

set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
LABEL="com.crawl4ai.runall"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CRON_MARK="# crawl4ai-runall"
DRY=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--dry-run" ]; then DRY=1; else ARGS+=("$a"); fi
done
set -- "${ARGS[@]+"${ARGS[@]}"}"
CMD="${1:-}"
OS="${SCHEDULE_OS:-$(uname -s)}"   # SCHEDULE_OS=Darwin/Linux only for testing with --dry-run

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# "30m" / "1h" / "10h" / "90" (minutes) -> minutes
to_minutes() {
  local v="$1" n
  case "$v" in
    *h) n="${v%h}" ;;
    *m) n="${v%m}" ;;
    *) n="$v" ;;
  esac
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "Can't read '$v'. Use e.g. 30m, 1h, 10h." >&2; exit 2; }
  case "$v" in *h) echo $((n * 60)) ;; *) echo "$n" ;; esac
}

check_time() { [[ "$1" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "Time must be HH:MM (24h), got '$1'" >&2; exit 2; }; }

# ── macOS: launchd ─────────────────────────────────────────────────────────
plist_xml() {   # $1 = "interval" minutes | "daily" "HH:MM ..."
  local kind="$1"; shift
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$PROJECT_DIR/run_all.sh</string><string>--scheduled</string></array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/scheduler.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/scheduler.log</string>
  <key>RunAtLoad</key><false/>
EOF
  if [ "$kind" = "interval" ]; then
    echo "  <key>StartInterval</key><integer>$(( $1 * 60 ))</integer>"
  else
    echo "  <key>StartCalendarInterval</key>"
    echo "  <array>"
    for t in "$@"; do
      local h="${t%%:*}" m="${t##*:}"
      echo "    <dict><key>Hour</key><integer>$((10#$h))</integer><key>Minute</key><integer>$((10#$m))</integer></dict>"
    done
    echo "  </array>"
  fi
  echo "</dict>"
  echo "</plist>"
}

mac_install() {
  local xml; xml="$(plist_xml "$@")"
  if [ "$DRY" = 1 ]; then echo "Would write $PLIST:"; echo "$xml"; return; fi
  mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  printf '%s\n' "$xml" > "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Scheduled with launchd ($PLIST)."
}

mac_off() {
  if [ "$DRY" = 1 ]; then echo "Would remove $PLIST"; return; fi
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Automatic runs are off."
}

mac_status() {
  if [ ! -f "$PLIST" ]; then echo "Automatic runs: off"; return; fi
  if grep -q StartInterval "$PLIST"; then
    local secs; secs="$(grep -A0 StartInterval "$PLIST" | sed -E 's/.*<integer>([0-9]+)<.*/\1/')"
    echo "Automatic runs: every $((secs / 60)) minutes"
  else
    echo "Automatic runs: daily at $(grep -o '<integer>[0-9]*</integer>' "$PLIST" | sed -E 's/<[^>]+>//g' | paste -d: - - | tr '\n' ' ')"
  fi
  launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state =|last exit code" | sed 's/^[[:space:]]*/  /' || echo "  (not loaded: run './schedule.sh' again to reload)"
}

# ── Linux: cron ───────────────────────────────────────────────────────────
cron_line() {   # $1 = "interval" minutes | "daily" times...
  local kind="$1"; shift
  local run="cd $PROJECT_DIR && /bin/bash ./run_all.sh --scheduled >> logs/scheduler.log 2>&1 $CRON_MARK"
  if [ "$kind" = "interval" ]; then
    local m="$1"
    if [ "$m" -lt 60 ]; then echo "*/$m * * * * $run"
    elif [ $((m % 60)) -eq 0 ] && [ "$m" -lt 1440 ]; then echo "0 */$((m / 60)) * * * $run"
    elif [ "$m" -eq 1440 ]; then echo "0 0 * * * $run"
    else echo "Linux/cron supports minutes below 60, whole hours below 24, or 24h. Got $m minutes." >&2; exit 2
    fi
  else
    for t in "$@"; do echo "$((10#${t##*:})) $((10#${t%%:*})) * * * $run"; done
  fi
}

cron_install() {
  local lines; lines="$(cron_line "$@")"
  if [ "$DRY" = 1 ]; then echo "Would add to crontab:"; echo "$lines"; return; fi
  mkdir -p "$PROJECT_DIR/logs"
  local others; others="$(crontab -l 2>/dev/null | grep -v "$CRON_MARK" || true)"   # read fully first: keep your other jobs
  { [ -n "$others" ] && printf '%s\n' "$others"; printf '%s\n' "$lines"; } | crontab -
  echo "Scheduled with cron (crontab -l to see it)."
}

cron_off() {
  if [ "$DRY" = 1 ]; then echo "Would remove the $CRON_MARK lines from crontab"; return; fi
  local others; others="$(crontab -l 2>/dev/null | grep -v "$CRON_MARK" || true)"
  if [ -n "$others" ]; then printf '%s\n' "$others" | crontab -; else crontab -r 2>/dev/null || true; fi
  echo "Automatic runs are off."
}

cron_status() {
  local lines; lines="$(crontab -l 2>/dev/null | grep "$CRON_MARK" || true)"
  if [ -z "$lines" ]; then echo "Automatic runs: off"; else echo "Automatic runs (cron):"; echo "$lines" | sed 's/^/  /'; fi
}

# ── commands ─────────────────────────────────────────────────────────────
install() {
  if [ "$OS" = "Darwin" ]; then mac_install "$@"; else cron_install "$@"; fi
  [ "$DRY" = 1 ] || { echo; status; }
}

status() {
  if [ "$OS" = "Darwin" ]; then mac_status; else cron_status; fi
  echo
  echo "Last runs (logs/history.log):"
  if [ -f "$PROJECT_DIR/logs/history.log" ]; then tail -n 5 "$PROJECT_DIR/logs/history.log" | sed 's/^/  /'; else echo "  none yet"; fi
}

case "$CMD" in
  every)
    [ $# -eq 2 ] || usage 2
    MIN="$(to_minutes "$2")"
    [ "$MIN" -ge 10 ] || { echo "Please use at least 10 minutes between runs." >&2; exit 2; }
    install interval "$MIN"
    [ "$DRY" = 1 ] || echo "Runs every $2. Logs: $PROJECT_DIR/logs/"
    ;;
  daily)
    [ $# -ge 2 ] || usage 2
    shift
    for t in "$@"; do check_time "$t"; done
    install daily "$@"
    [ "$DRY" = 1 ] || echo "Runs daily at: $*. Logs: $PROJECT_DIR/logs/"
    ;;
  off|remove|stop) if [ "$OS" = "Darwin" ]; then mac_off; else cron_off; fi ;;
  status) status ;;
  run-now)
    if [ "$OS" = "Darwin" ] && [ -f "$PLIST" ] && [ "$DRY" = 0 ]; then
      launchctl kickstart "gui/$(id -u)/$LABEL" && echo "Started. Follow it with: tail -f $PROJECT_DIR/logs/latest.log"
    else
      nohup /bin/bash "$PROJECT_DIR/run_all.sh" --scheduled >/dev/null 2>&1 &
      echo "Started in the background. Follow it with: tail -f $PROJECT_DIR/logs/latest.log"
    fi
    ;;
  ""|-h|--help|help) usage 0 ;;
  *) echo "Unknown command '$CMD'."; usage 2 ;;
esac
