#!/bin/bash
# Start, stop and check the web app (dashboard, articles, foresight, datasets, charts, reports) on this Mac.
#
#   ./dashboard.sh             start it in the background and open http://localhost:8060/dashboard/
#   ./dashboard.sh stop        stop it
#   ./dashboard.sh restart     stop, then start
#   ./dashboard.sh status      is it running, and the last lines of its log
#   ./dashboard.sh open        open it in the browser (starts it first if it isn't running)
#   ./dashboard.sh log         follow its log (Ctrl+C stops following, not the server)
#   ./dashboard.sh run         run it in this window instead; Ctrl+C stops it
#   --no-open                  after start / restart: don't open the browser
#
# It keeps running when this Terminal window is closed, until ./dashboard.sh stop or the Mac restarts.
# It listens on this computer only (127.0.0.1), and reloads itself after a code change.
# Another port: WEBAPP_PORT=8061 ./dashboard.sh, or WEBAPP_PORT="8061" in automation.conf.
# Log: logs/webapp.log (a new one is started once it passes 5 MB; the old one is kept as webapp.log.1).

set -u
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
PORT_FROM_COMMAND="${WEBAPP_PORT:-}"
source ./env.sh
[ -f ./automation.conf ] && source ./automation.conf
PORT="${PORT_FROM_COMMAND:-${WEBAPP_PORT:-8060}}"            # the command line wins over automation.conf
HOST="127.0.0.1"
URL="http://localhost:$PORT"
LOG_DIR="${DASHBOARD_LOG_DIR:-$PROJECT_DIR/logs}"
LOG="$LOG_DIR/webapp.log"
UV="${UV:-$(command -v uv || true)}"
PATTERN="webapp runserver $HOST:$PORT"                       # how our own server is told apart from anything else
WAIT_SECONDS=40

say() { echo "$*"; }

our_pids() { pgrep -f "$PATTERN" 2>/dev/null; }

answers() {
  local code
  code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$URL/login/" 2>/dev/null)"
  [ "$code" = "200" ] || [ "$code" = "302" ]
}

port_command() {
  local pid
  pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  [ -n "$pid" ] && ps -o command= -p "$pid" 2>/dev/null
}

port_owner() { port_command | cut -c1-100; }                 # short enough to show

open_browser() {
  if command -v open >/dev/null 2>&1; then open "$URL/dashboard/"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL/dashboard/" >/dev/null 2>&1
  else say "Open $URL/dashboard/ in your browser."; fi
}

ready_to_start() {
  if [ -z "$UV" ]; then
    say "uv isn't installed or isn't on the PATH. Install it: https://docs.astral.sh/uv/ , then run this again."
    return 1
  fi
  # PostgreSQL holds everything the pages show: without it they can't open.
  if command -v pg_isready >/dev/null 2>&1 && ! pg_isready -q >/dev/null 2>&1; then
    say "PostgreSQL isn't running, so the web app has nothing to show. Start it:"
    say "    brew services start postgresql@18"
    say "then run ./dashboard.sh again."
    return 1
  fi
  local other
  other="$(port_owner)"
  if [ -n "$other" ]; then
    say "Port $PORT is used by another program: $other"
    say "Stop that program, or use another port: WEBAPP_PORT=8061 ./dashboard.sh"
    return 1
  fi
  # Migrations aren't applied here: the project's rule is a backup first. Pages may fail until they are.
  if ! "$UV" run --frozen python -m webapp migrate --check >/dev/null 2>&1; then
    say "Note: the web app has database changes waiting. Some pages may fail until you run:"
    say "    uv run python -m warehouse backup && uv run python -m webapp migrate"
  fi
  return 0
}

keep_log_small() {
  mkdir -p "$LOG_DIR"
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 5000000 ]; then mv -f "$LOG" "$LOG.1"; fi
}

by_hand() { port_command | grep -q "webapp runserver"; }    # the whole command: a long path would hide it in the short one      # the web app, started some other way (runserver 8060 in a Terminal)

start() {
  local want_open="$1" pid i
  if [ -z "$(our_pids)" ] && by_hand && answers; then
    say "Already running at $URL/dashboard/ (started another way, e.g. runserver in a Terminal window)."
    [ "$want_open" = 1 ] && open_browser
    return 0
  fi
  if [ -n "$(our_pids)" ]; then
    if answers; then
      say "Already running at $URL/dashboard/"
      [ "$want_open" = 1 ] && open_browser
      return 0
    fi
    say "It's running but not answering yet. Wait a few seconds, or see the log: ./dashboard.sh log"
    return 1
  fi
  ready_to_start || return 1
  keep_log_small
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting on $HOST:$PORT" >> "$LOG"
  nohup "$UV" run --frozen python -m webapp runserver "$HOST:$PORT" >> "$LOG" 2>&1 < /dev/null &
  pid=$!
  printf "Starting the web app"
  for i in $(seq 1 $((WAIT_SECONDS * 2))); do
    if answers; then
      echo
      say "Running at $URL/dashboard/ (sign in with your account)."
      say "Stop it with ./dashboard.sh stop. Its log: logs/webapp.log"
      [ "$want_open" = 1 ] && open_browser
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo
      say "The web app stopped while starting. The end of its log:"
      tail -n 15 "$LOG" | sed 's/^/    /'
      return 1
    fi
    [ $((i % 4)) -eq 0 ] && printf "."
    sleep 0.5
  done
  echo
  say "It hasn't answered after ${WAIT_SECONDS} seconds. It may still be starting: ./dashboard.sh status, or see ./dashboard.sh log"
  return 1
}

stop() {
  local pids i
  pids="$(our_pids)"
  if [ -z "$pids" ] && by_hand; then
    say "The web app on port $PORT was started another way, not by this script. Press Ctrl+C in the Terminal window where it runs,"
    say "or stop it with: kill $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t | head -1)"
    return 1
  fi
  if [ -z "$pids" ]; then
    say "The web app isn't running."
    return 0
  fi
  # uv, Python and Django's reloader all carry the same command line, so all three stop together
  kill -TERM $pids 2>/dev/null
  for i in $(seq 1 20); do
    [ -z "$(our_pids)" ] && break
    sleep 0.5
  done
  pids="$(our_pids)"
  [ -n "$pids" ] && kill -KILL $pids 2>/dev/null
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') stopped" >> "$LOG" 2>/dev/null
  say "Stopped."
}

status() {
  local pids
  pids="$(our_pids)"
  if [ -n "$pids" ] && answers; then
    say "Running at $URL/dashboard/ (since $(ps -o lstart= -p "$(echo "$pids" | head -1)" 2>/dev/null | sed 's/  */ /g; s/ *$//'))."
  elif by_hand && answers; then
    say "Running at $URL/dashboard/, started another way (not by this script)."
  elif [ -n "$pids" ]; then
    say "Started, but not answering at $URL. It may still be starting, or be stuck: see ./dashboard.sh log"
  else
    say "Not running. Start it with ./dashboard.sh"
  fi
  if [ -f "$LOG" ]; then
    say "Last lines of logs/webapp.log:"
    tail -n 5 "$LOG" | sed 's/^/    /'
  fi
  [ -n "$pids" ] || by_hand
}

run_here() {
  if [ -n "$(our_pids)" ]; then
    say "It's already running in the background at $URL/dashboard/. Stop it first: ./dashboard.sh stop"
    return 1
  fi
  ready_to_start || return 1
  say "Running in this window at $URL/dashboard/ . Ctrl+C stops it."
  exec "$UV" run --frozen python -m webapp runserver "$HOST:$PORT"
}

COMMAND="start"
WANT_OPEN=1
for arg in "$@"; do
  case "$arg" in
    --no-open) WANT_OPEN=0 ;;
    start|stop|restart|status|open|log|run) COMMAND="$arg" ;;
    -h|--help|help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) say "Unknown: $arg. Try: ./dashboard.sh help"; exit 2 ;;
  esac
done

case "$COMMAND" in
  start) start "$WANT_OPEN" ;;
  stop) stop ;;
  restart) stop && start "$WANT_OPEN" ;;
  status) status ;;
  open) start 1 ;;
  log) mkdir -p "$LOG_DIR"; touch "$LOG"; tail -n 40 -f "$LOG" ;;
  run) run_here ;;
esac
