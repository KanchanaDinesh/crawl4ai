#!/usr/bin/env bash
# Run every collector once: markets -> news -> tech intel (see automation.conf).
#
#   ./run_all.sh                 run the default steps
#   ./run_all.sh markets news    run only these steps
#   ./run_all.sh --list          show the steps and their commands
#   ./run_all.sh --status        show the last runs
#
# Safe to run by hand or from the scheduler (./schedule.sh):
#   - each step runs even if an earlier one failed; the exit code is non-zero if any failed
#   - output goes to the screen and to logs/run-<date>-<time>.log; a one-line summary per run to logs/history.log
#   - if a previous run is still going, this one exits straight away (no overlapping runs)

set -uo pipefail
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
LOG_DIR="$PROJECT_DIR/logs"
SCHEDULED=0
LIST=0
STATUS=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --scheduled) SCHEDULED=1 ;;
    --list) LIST=1 ;;
    --status) STATUS=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Unknown option: $a (try --help)"; exit 2 ;;
    *) ARGS+=("$a") ;;
  esac
done

mkdir -p "$LOG_DIR"
if [ "$STATUS" = 1 ]; then
  if [ -f "$LOG_DIR/history.log" ]; then tail -n 15 "$LOG_DIR/history.log"; else echo "No runs yet."; fi
  exit 0
fi

# Keep tools and caches inside the project folder (see env.sh)
# shellcheck disable=SC1091
source ./env.sh
# shellcheck disable=SC1091
[ -f ./automation.conf ] && source ./automation.conf

# Find uv even when started by the scheduler (which has a minimal PATH)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# Default command for each step (automation.conf can override STEP_<name>)
: "${STEP_markets:=$UV run --frozen python -m markets run ${MARKETS_ARGS:-}}"
: "${STEP_news:=$UV run --frozen python -m worldnews run ${NEWS_ARGS:-}}"
: "${STEP_tech:=$UV run --frozen python -m techintel run ${TECH_ARGS:-}}"
: "${STEP_vehicles:=$UV run --frozen python -m scrapers.vehicles --quiet ${VEHICLES_ARGS:-}}"
: "${STEP_lknews:=$UV run --frozen python -m scrapers.news ${LKNEWS_ARGS:-}}"

if [ ${#ARGS[@]} -gt 0 ]; then STEP_LIST="${ARGS[*]}"; else STEP_LIST="${STEPS:-markets news tech}"; fi

step_cmd() { local v="STEP_$1"; printf '%s' "${!v:-}"; }

if [ "$LIST" = 1 ]; then
  echo "Default steps: ${STEPS:-markets news tech}"
  seen=""
  for s in markets news tech vehicles lknews $STEP_LIST; do
    case " $seen " in *" $s "*) continue ;; esac; seen="$seen $s"
    printf '  %-9s %s\n' "$s" "$(step_cmd "$s")"
  done
  exit 0
fi

for s in $STEP_LIST; do
  if [ -z "$(step_cmd "$s")" ]; then echo "Unknown step '$s'. Try: ./run_all.sh --list"; exit 2; fi
done

# One run at a time: a lock folder holding the running process id
LOCK="$LOG_DIR/.run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  OTHER="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$OTHER" ] && kill -0 "$OTHER" 2>/dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S')  skipped: another run is still going (pid $OTHER)" | tee -a "$LOG_DIR/history.log"
    exit 0
  fi
  rm -rf "$LOCK"                                   # left over from a run that was killed
  mkdir "$LOCK" || { echo "Could not create $LOCK"; exit 1; }
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
trap 'echo; echo "Stopped."; exit 130' INT TERM

STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG="$LOG_DIR/run-$STAMP.log"
if [ "$SCHEDULED" = 1 ]; then
  exec >>"$LOG" 2>&1                               # scheduled: log file only
else
  exec > >(tee -a "$LOG") 2>&1                     # by hand: screen and log file
fi
ln -sf "run-$STAMP.log" "$LOG_DIR/latest.log"

fmt_secs() { local s=$1; if [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s / 60)) $((s % 60)); else printf '%ds' "$s"; fi; }

START=$(date +%s)
echo "=== run started $(date '+%Y-%m-%d %H:%M:%S')  steps: $STEP_LIST  ($([ "$SCHEDULED" = 1 ] && echo scheduled || echo manual)) ==="
SUMMARY=""
FAILED=0
for s in $STEP_LIST; do
  CMD="$(step_cmd "$s")"
  echo
  echo "--- [$s] $CMD"
  T0=$(date +%s)
  bash -c "$CMD"
  RC=$?
  DUR=$(( $(date +%s) - T0 ))
  if [ $RC -eq 0 ]; then STATE="ok"; else STATE="FAILED(exit $RC)"; FAILED=$((FAILED + 1)); fi
  echo "--- [$s] $STATE in $(fmt_secs $DUR)"
  SUMMARY="$SUMMARY | $s $STATE $(fmt_secs $DUR)"
  if [ $RC -eq 130 ]; then break; fi               # Ctrl+C inside a step: stop the whole run
done

TOTAL=$(( $(date +%s) - START ))
RESULT=$([ $FAILED -eq 0 ] && echo "ok" || echo "$FAILED step(s) failed")
LINE="$(date '+%Y-%m-%d %H:%M:%S')  $RESULT  total $(fmt_secs $TOTAL)${SUMMARY}  log: logs/run-$STAMP.log"
echo
echo "=== $LINE"
echo "$LINE" >> "$LOG_DIR/history.log"

# Tidy up old logs
find "$LOG_DIR" -name 'run-*.log' -type f -mtime +"${KEEP_LOG_DAYS:-30}" -delete 2>/dev/null || true

[ $FAILED -eq 0 ]
