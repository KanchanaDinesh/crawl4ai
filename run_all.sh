#!/usr/bin/env bash
# Run the collectors once (see automation.conf for steps and profiles).
#
#   ./run_all.sh                       run the default steps (STEPS in automation.conf)
#   ./run_all.sh --profile frequent    run a profile (PROFILE_frequent: tech_quick vehicles)
#   ./run_all.sh markets vehicles      run only these steps
#   ./run_all.sh --list                show steps, profiles and their commands
#   ./run_all.sh --status              show the last runs
#
# Safe to run by hand or from the scheduler (./schedule.sh):
#   - each step runs even if an earlier one failed; the exit code is non-zero if any failed
#   - output goes to the screen and logs/run-<date>-<time>-<profile>.log; one summary line per run in logs/history.log
#   - one lock per tool: if a step's tool is busy (another run is still using it), that step is skipped

set -uo pipefail
cd "$(dirname "$0")" || exit 1
PROJECT_DIR="$(pwd)"
LOG_DIR="$PROJECT_DIR/logs"
SCHEDULED=0; LIST=0; STATUS=0; PROFILE=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --scheduled) SCHEDULED=1 ;;
    --list) LIST=1 ;;
    --status) STATUS=1 ;;
    --profile) shift; PROFILE="${1:-}"; [ -n "$PROFILE" ] || { echo "--profile needs a name"; exit 2; } ;;
    --profile=*) PROFILE="${1#--profile=}" ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Unknown option: $1 (try --help)"; exit 2 ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done

mkdir -p "$LOG_DIR"
if [ "$STATUS" = 1 ]; then
  if [ -f "$LOG_DIR/history.log" ]; then tail -n 15 "$LOG_DIR/history.log"; else echo "No runs yet."; fi
  exit 0
fi

# shellcheck disable=SC1091
source ./env.sh                                    # keep tools and caches inside the project folder
# shellcheck disable=SC1091
[ -f ./automation.conf ] && source ./automation.conf

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then echo "uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; fi

# Default command and tool (lock) for each step; automation.conf can override STEP_<name> / TOOL_<name>
: "${STEP_markets:=$UV run --frozen python -m markets run ${MARKETS_ARGS:-}}"
: "${STEP_news:=$UV run --frozen python -m worldnews run ${NEWS_ARGS:-}}"
: "${STEP_tech:=$UV run --frozen python -m techintel run ${TECH_ARGS:-}}"
: "${STEP_tech_quick:=$UV run --frozen python -m techintel quick ${TECH_QUICK_ARGS:-}}"
: "${STEP_vehicles:=$UV run --frozen python -m scrapers.vehicles --quiet ${VEHICLES_ARGS:-}}"
: "${STEP_lknews:=$UV run --frozen python -m scrapers.news ${LKNEWS_ARGS:-}}"
: "${TOOL_markets:=markets}" "${TOOL_news:=news}" "${TOOL_tech:=tech}" "${TOOL_tech_quick:=tech}"
: "${TOOL_vehicles:=scrapers}" "${TOOL_lknews:=scrapers}"

step_cmd() { local v="STEP_$1"; printf '%s' "${!v:-}"; }
step_tool() { local v="TOOL_$1"; printf '%s' "${!v:-$1}"; }

if [ -n "$PROFILE" ]; then
  pv="PROFILE_$PROFILE"
  STEP_LIST="${!pv:-}"
  [ -n "$STEP_LIST" ] || { echo "Unknown profile '$PROFILE'. Add PROFILE_$PROFILE=\"...\" to automation.conf."; exit 2; }
elif [ ${#ARGS[@]} -gt 0 ]; then
  STEP_LIST="${ARGS[*]}"; PROFILE="custom"
else
  STEP_LIST="${STEPS:-markets news tech vehicles}"; PROFILE="default"
fi

if [ "$LIST" = 1 ]; then
  echo "Default steps:  ${STEPS:-markets news tech vehicles}"
  for v in $(compgen -v PROFILE_); do echo "Profile ${v#PROFILE_}:$(printf '%*s' $((10 - ${#v} + 8)) '') ${!v}"; done
  echo; echo "Steps:"
  seen=""
  for s in markets news tech tech_quick vehicles lknews $STEP_LIST; do
    case " $seen " in *" $s "*) continue ;; esac; seen="$seen $s"
    printf '  %-11s [%s]  %s\n' "$s" "$(step_tool "$s")" "$(step_cmd "$s")"
  done
  exit 0
fi

for s in $STEP_LIST; do
  if [ -z "$(step_cmd "$s")" ]; then echo "Unknown step '$s'. Try: ./run_all.sh --list"; exit 2; fi
done

# ── per-tool locks: a folder holding the pid of the run using that tool ──
HELD=""
take_lock() {   # $1 = tool; returns 1 if another live run holds it
  local lock="$LOG_DIR/.lock-$1"
  if ! mkdir "$lock" 2>/dev/null; then
    local other; other="$(cat "$lock/pid" 2>/dev/null || true)"
    if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then BUSY_PID="$other"; return 1; fi
    rm -rf "$lock"                                 # left over from a run that was killed
    mkdir "$lock" 2>/dev/null || { BUSY_PID="?"; return 1; }
  fi
  echo $$ > "$lock/pid"; HELD="$lock"; return 0
}
release_lock() { [ -n "$HELD" ] && rm -rf "$HELD"; HELD=""; }
trap 'release_lock' EXIT
trap 'echo; echo "Stopped."; exit 130' INT TERM

STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG="$LOG_DIR/run-$STAMP-$PROFILE.log"
if [ "$SCHEDULED" = 1 ]; then exec >>"$LOG" 2>&1; else exec > >(tee -a "$LOG") 2>&1; fi
ln -sf "run-$STAMP-$PROFILE.log" "$LOG_DIR/latest.log"

fmt_secs() { local s=$1; if [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s / 60)) $((s % 60)); else printf '%ds' "$s"; fi; }

START=$(date +%s)
echo "=== run started $(date '+%Y-%m-%d %H:%M:%S')  profile: $PROFILE  steps: $STEP_LIST  ($([ "$SCHEDULED" = 1 ] && echo scheduled || echo manual)) ==="
SUMMARY=""; FAILED=0; SKIPPED=0
for s in $STEP_LIST; do
  CMD="$(step_cmd "$s")"; TOOL="$(step_tool "$s")"
  echo
  if ! take_lock "$TOOL"; then
    echo "--- [$s] skipped: '$TOOL' is busy with another run (pid $BUSY_PID)"
    SUMMARY="$SUMMARY | $s skipped(busy)"; SKIPPED=$((SKIPPED + 1))
    continue
  fi
  echo "--- [$s] $CMD"
  T0=$(date +%s)
  bash -c "$CMD"
  RC=$?
  release_lock
  DUR=$(( $(date +%s) - T0 ))
  if [ $RC -eq 0 ]; then STATE="ok"; else STATE="FAILED(exit $RC)"; FAILED=$((FAILED + 1)); fi
  echo "--- [$s] $STATE in $(fmt_secs $DUR)"
  SUMMARY="$SUMMARY | $s $STATE $(fmt_secs $DUR)"
  if [ $RC -eq 130 ]; then break; fi               # Ctrl+C inside a step: stop the whole run
done

TOTAL=$(( $(date +%s) - START ))
if [ $FAILED -gt 0 ]; then RESULT="$FAILED step(s) failed"; elif [ $SKIPPED -gt 0 ]; then RESULT="ok, $SKIPPED skipped"; else RESULT="ok"; fi
LINE="$(date '+%Y-%m-%d %H:%M:%S')  $RESULT  [$PROFILE] total $(fmt_secs $TOTAL)${SUMMARY}  log: logs/run-$STAMP-$PROFILE.log"
echo
echo "=== $LINE"
echo "$LINE" >> "$LOG_DIR/history.log"

find "$LOG_DIR" -name 'run-*.log' -type f -mtime +"${KEEP_LOG_DAYS:-30}" -delete 2>/dev/null || true
[ $FAILED -eq 0 ]
