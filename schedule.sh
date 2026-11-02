#!/usr/bin/env bash
# Run ./run_all.sh automatically, and keep the live tech-intel listener running.
#
#   ./schedule.sh recommended                    set up everything below in one go (listener + 15m + hourly)
#   ./schedule.sh every 15m --profile frequent   every 15 minutes: tech_quick + vehicles
#   ./schedule.sh every 1h  --profile hourly     every hour: markets + news + full tech intel
#   ./schedule.sh every 2h                       the default steps (STEPS in automation.conf)
#   ./schedule.sh daily 07:30 19:30 [--profile NAME]
#   ./schedule.sh watch on|off|status            the always-on certificate listener
#   ./schedule.sh status                         all schedules, the listener, the last runs
#   ./schedule.sh off [--profile NAME | all]     stop one schedule (or every one)
#   ./schedule.sh run-now [--profile NAME]       start one run now, in the background
#   ./schedule.sh doctor                         run the browser health check the way scheduled runs see it
#
# Profiles are defined in automation.conf. macOS uses launchd (LaunchAgents: they run while you're
# logged in; a run missed during sleep happens on wake). Linux uses your crontab and leaves your
# other cron jobs alone. Add --dry-run to see what would be set up without changing anything.

set -euo pipefail
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
OS="${SCHEDULE_OS:-$(uname -s)}"   # SCHEDULE_OS=Darwin/Linux only for testing with --dry-run
AGENTS="$HOME/Library/LaunchAgents"
WATCH_LABEL="com.crawl4ai.techwatch"
DRY=0; PROFILE="default"; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --profile) shift; PROFILE="${1:-}"; [ -n "$PROFILE" ] || { echo "--profile needs a name" >&2; exit 2; } ;;
    --profile=*) PROFILE="${1#--profile=}" ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
set -- "${ARGS[@]+"${ARGS[@]}"}"
CMD="${1:-}"

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
label_for() { if [ "$1" = "default" ]; then echo "com.crawl4ai.runall"; else echo "com.crawl4ai.runall.$1"; fi; }
mark_for() { echo "# crawl4ai-runall[$1]"; }
WATCH_MARK="# crawl4ai-watch"
run_args() { if [ "$1" = "default" ]; then echo "--scheduled"; else echo "--scheduled --profile $1"; fi; }

check_profile() {
  [ "$PROFILE" = "default" ] && return
  [[ "$PROFILE" =~ ^[A-Za-z0-9_]+$ ]] || { echo "Profile names use letters, digits and _ only." >&2; exit 2; }
  grep -q "^PROFILE_$PROFILE=" automation.conf 2>/dev/null || { echo "No profile '$PROFILE' in automation.conf (add PROFILE_$PROFILE=\"...\")." >&2; exit 2; }
}

to_minutes() {   # "30m" / "1h" / "10h" / "90" -> minutes
  local v="$1" n
  case "$v" in *h) n="${v%h}" ;; *m) n="${v%m}" ;; *) n="$v" ;; esac
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "Can't read '$v'. Use e.g. 15m, 1h, 10h." >&2; exit 2; }
  case "$v" in *h) echo $((n * 60)) ;; *) echo "$n" ;; esac
}
check_time() { [[ "$1" =~ ^([01]?[0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "Time must be HH:MM (24h), got '$1'" >&2; exit 2; }; }

PATH_FOR_JOBS="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── macOS: launchd ─────────────────────────────────────────────────────────
plist_head() {   # $1 label, $2.. program arguments
  local label="$1"; shift
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>$(for a in "$@"; do printf '<string>%s</string>' "$a"; done)</array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_FOR_JOBS</string></dict>
EOF
}

run_plist() {   # $1 = "interval" minutes | "daily" times...
  local kind="$1"; shift
  # shellcheck disable=SC2046
  plist_head "$(label_for "$PROFILE")" /bin/bash "$PROJECT_DIR/run_all.sh" $(run_args "$PROFILE")
  echo "  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/scheduler.log</string>"
  echo "  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/scheduler.log</string>"
  echo "  <key>RunAtLoad</key><false/>"
  if [ "$kind" = "interval" ]; then
    echo "  <key>StartInterval</key><integer>$(( $1 * 60 ))</integer>"
  else
    echo "  <key>StartCalendarInterval</key>"
    echo "  <array>"
    for t in "$@"; do
      echo "    <dict><key>Hour</key><integer>$((10#${t%%:*}))</integer><key>Minute</key><integer>$((10#${t##*:}))</integer></dict>"
    done
    echo "  </array>"
  fi
  echo "</dict>"; echo "</plist>"
}

watch_plist() {
  plist_head "$WATCH_LABEL" /bin/bash "$PROJECT_DIR/watch.sh" --foreground
  echo "  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/watch.log</string>"
  echo "  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/watch.log</string>"
  echo "  <key>RunAtLoad</key><true/>"
  echo "  <key>KeepAlive</key><true/>"
  echo "  <key>ThrottleInterval</key><integer>60</integer>"
  echo "</dict>"; echo "</plist>"
}

mac_load() {   # $1 label, stdin = plist
  local label="$1" file="$AGENTS/$1.plist" xml; xml="$(cat)"
  if [ "$DRY" = 1 ]; then echo "Would write $file:"; echo "$xml"; return; fi
  mkdir -p "$AGENTS" "$PROJECT_DIR/logs"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  printf '%s\n' "$xml" > "$file"
  launchctl bootstrap "gui/$(id -u)" "$file"
}
mac_unload() {   # $1 label
  local file="$AGENTS/$1.plist"
  if [ "$DRY" = 1 ]; then echo "Would remove $file"; return; fi
  launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
  rm -f "$file"
}
mac_describe() {   # $1 plist file
  local f="$1" name; name="$(basename "$f" .plist)"
  if grep -q StartInterval "$f"; then
    echo "  $name: every $(( $(sed -nE 's/.*StartInterval<\/key><integer>([0-9]+)<.*/\1/p' "$f") / 60 )) minutes"
  else
    echo "  $name: daily at $(grep -oE '<integer>[0-9]+</integer>' "$f" | sed -E 's/<[^>]+>//g' | paste -d: - - | sed -E 's/:([0-9])$/:0\1/' | tr '\n' ' ')"
  fi
}

# Run doctor.py once inside the scheduler (launchd on a Mac, a cron-like bare environment on Linux), with
# Chromium's own messages, because scheduled runs have a different environment and permissions than Terminal.
DOCTOR_LABEL="com.crawl4ai.doctor"
scheduled_doctor() {
  local log="$PROJECT_DIR/logs/doctor-scheduled.log"
  local cmd="cd '$PROJECT_DIR' && uv run --frozen python doctor.py --verbose; echo \"=== doctor finished (exit \$?) ===\""
  if [ "$DRY" = 1 ]; then echo "Would run in the scheduler: $cmd  > $log"; return; fi
  mkdir -p "$PROJECT_DIR/logs"; : > "$log"
  if [ "$OS" = "Darwin" ]; then
    { plist_head "$DOCTOR_LABEL" /bin/bash -c "$cmd"
      echo "  <key>StandardOutPath</key><string>$log</string>"
      echo "  <key>StandardErrorPath</key><string>$log</string>"
      echo "  <key>RunAtLoad</key><true/>"
      echo "</dict>"; echo "</plist>"; } | mac_load "$DOCTOR_LABEL"
  else
    env -i HOME="$HOME" USER="${USER:-}" PATH="$PATH_FOR_JOBS" /bin/bash -c "$cmd" >"$log" 2>&1 </dev/null &
  fi
  echo "Running the health check in the scheduler (up to 3 minutes)..."
  local i=0
  while [ $i -lt 180 ] && ! grep -q "=== doctor finished" "$log" 2>/dev/null; do sleep 1; i=$((i + 1)); done
  [ "$OS" = "Darwin" ] && mac_unload "$DOCTOR_LABEL"
  # the verdict lines, plus only the Chromium messages that explain a crash (the rest is normal noise)
  echo; grep -vE "^\s*$" "$log" | grep -E -v "pw:browser" | tail -60
  local why; why="$(grep -E "pw:browser" "$log" | grep -iE "exitCode=[1-9]|signal=[A-Z]|FATAL|Check failed|crash|denied|not permitted|killed|sandbox" | cut -c1-240 | tail -15)"
  [ -n "$why" ] && { echo; echo "Chromium said:"; echo "$why"; }
  grep -q "=== doctor finished" "$log" || echo "(it did not finish within 3 minutes; full output: logs/doctor-scheduled.log)"
  echo; echo "Full output: logs/doctor-scheduled.log"
}

# ── Linux: cron ───────────────────────────────────────────────────────────
cron_lines_run() {   # $1 = "interval" minutes | "daily" times...
  local kind="$1"; shift
  local run; run="cd $PROJECT_DIR && /bin/bash ./run_all.sh $(run_args "$PROFILE") >> logs/scheduler.log 2>&1 $(mark_for "$PROFILE")"
  if [ "$kind" = "interval" ]; then
    local m="$1"
    if [ "$m" -lt 60 ]; then echo "*/$m * * * * $run"
    elif [ $((m % 60)) -eq 0 ] && [ "$m" -lt 1440 ]; then echo "0 */$((m / 60)) * * * $run"
    elif [ "$m" -eq 1440 ]; then echo "0 0 * * * $run"
    else echo "With cron, use minutes below 60, whole hours below 24, or 24h. Got $m minutes." >&2; exit 2
    fi
  else
    for t in "$@"; do echo "$((10#${t##*:})) $((10#${t%%:*})) * * * $run"; done
  fi
}
cron_replace() {   # $1 = marker to replace; stdin = new lines (may be empty)
  local mark="$1" new others; new="$(cat)"
  others="$(crontab -l 2>/dev/null | grep -vF "$mark" || true)"   # read fully first: keep your other jobs
  if [ "$DRY" = 1 ]; then
    if [ -n "$new" ]; then echo "Would set these crontab lines:"; echo "$new"; else echo "Would remove the '$mark' lines from crontab"; fi
    return
  fi
  mkdir -p "$PROJECT_DIR/logs"
  if [ -z "$others$new" ]; then crontab -r 2>/dev/null || true; return; fi
  {
    if [ -n "$others" ]; then printf '%s\n' "$others"; fi
    if [ -n "$new" ]; then printf '%s\n' "$new"; fi
  } | crontab -
}

# ── commands ─────────────────────────────────────────────────────────────
schedule() {   # "interval" minutes | "daily" times...
  check_profile
  if [ "$OS" = "Darwin" ]; then run_plist "$@" | mac_load "$(label_for "$PROFILE")"
  else cron_lines_run "$@" | cron_replace "$(mark_for "$PROFILE")"; fi
  if [ "$DRY" = 0 ]; then echo "Scheduled: profile '$PROFILE'. Logs: $PROJECT_DIR/logs/"; echo; status; fi
}

stop_schedule() {
  if [ "${1:-}" = "all" ]; then
    if [ "$OS" = "Darwin" ]; then
      for f in "$AGENTS"/com.crawl4ai.runall*.plist; do [ -e "$f" ] && mac_unload "$(basename "$f" .plist)"; done
    else
      printf '' | cron_replace "# crawl4ai-runall["
    fi
    echo "All schedules are off (the listener is separate: ./schedule.sh watch off)."
    return
  fi
  if [ "$OS" = "Darwin" ]; then mac_unload "$(label_for "$PROFILE")"; else printf '' | cron_replace "$(mark_for "$PROFILE")"; fi
  echo "Schedule for profile '$PROFILE' is off."
}

watch_cmd() {
  case "${1:-status}" in
    on)
      if [ "$OS" = "Darwin" ]; then
        watch_plist | mac_load "$WATCH_LABEL"
      else
        printf '%s\n' "@reboot cd $PROJECT_DIR && /bin/bash ./watch.sh --ensure $WATCH_MARK" \
                      "*/5 * * * * cd $PROJECT_DIR && /bin/bash ./watch.sh --ensure $WATCH_MARK" | cron_replace "$WATCH_MARK"
        [ "$DRY" = 1 ] || /bin/bash ./watch.sh --ensure
      fi
      [ "$DRY" = 1 ] || echo "Listener is on: it restarts automatically. New domains are added by the 'frequent' profile (tech_quick)."
      ;;
    off)
      if [ "$OS" = "Darwin" ]; then mac_unload "$WATCH_LABEL"; else printf '' | cron_replace "$WATCH_MARK"; fi
      [ "$DRY" = 1 ] || { /bin/bash ./watch.sh --stop >/dev/null 2>&1 || true; echo "Listener is off."; }
      ;;
    status) /bin/bash ./watch.sh --status ;;
    *) echo "Use: ./schedule.sh watch on|off|status" >&2; exit 2 ;;
  esac
}

status() {
  echo "Schedules:"
  if [ "$OS" = "Darwin" ]; then
    local any=0
    for f in "$AGENTS"/com.crawl4ai.runall*.plist; do [ -e "$f" ] && { any=1; mac_describe "$f"; }; done
    [ "$any" = 1 ] || echo "  none"
    [ -e "$AGENTS/$WATCH_LABEL.plist" ] && echo "  $WATCH_LABEL: listener, always on"
  else
    local lines; lines="$(crontab -l 2>/dev/null | grep -F "# crawl4ai-" || true)"
    if [ -n "$lines" ]; then echo "$lines" | sed -E 's/cd [^&]*&& //; s/>> logs[^#]*//; s/^/  /'; else echo "  none"; fi
  fi
  echo
  watch_cmd status
  echo
  echo "Last runs (logs/history.log):"
  if [ -f "$PROJECT_DIR/logs/history.log" ]; then tail -n 6 "$PROJECT_DIR/logs/history.log" | cut -c1-150 | sed 's/^/  /'; else echo "  none yet"; fi
}

case "$CMD" in
  every)
    [ $# -eq 2 ] || usage 2
    MIN="$(to_minutes "$2")"
    [ "$MIN" -ge 5 ] || { echo "Please use at least 5 minutes between runs." >&2; exit 2; }
    schedule interval "$MIN"
    ;;
  daily)
    [ $# -ge 2 ] || usage 2
    shift; for t in "$@"; do check_time "$t"; done
    schedule daily "$@"
    ;;
  watch) watch_cmd "${2:-status}" ;;
  recommended|setup)
    # Tech intel runs all the time: the listener catches new domains live, tech_quick qualifies them every
    # 15 minutes (with vehicles), and the hourly run does the deep work (profiles, monitoring) plus markets + news.
    dry=(); [ "$DRY" = 1 ] && dry=(--dry-run)
    for args in "watch on" "every 15m --profile frequent" "every 1h --profile hourly"; do
      echo "── ./schedule.sh $args"
      # shellcheck disable=SC2086
      "$0" $args "${dry[@]+"${dry[@]}"}" || exit $?
    done
    [ "$DRY" = 1 ] || { echo; "$0" status; }
    ;;
  off|remove|stop) stop_schedule "${2:-}" ;;
  status) status ;;
  doctor) scheduled_doctor ;;
  run-now)
    check_profile
    # shellcheck disable=SC2046
    nohup /bin/bash "$PROJECT_DIR/run_all.sh" $(run_args "$PROFILE") >/dev/null 2>&1 &
    echo "Started profile '$PROFILE' in the background. Follow it with: tail -f $PROJECT_DIR/logs/latest.log"
    ;;
  ""|-h|--help|help) usage 0 ;;
  *) echo "Unknown command '$CMD'."; usage 2 ;;
esac
