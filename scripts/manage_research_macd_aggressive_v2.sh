#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PID_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.pid"
LOCK_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.lock"
OUT_FILE="${REPO_ROOT}/logs/macd_aggressive_research_v2.out"
HEARTBEAT_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2_heartbeat.json"
STOP_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.stop"
LOCAL_ENV="${REPO_ROOT}/config/research_v2.env"
RUNNER="${REPO_ROOT}/scripts/run_research_macd_aggressive_v2.sh"
STARTUP_TIMEOUT_SECONDS="${MACD_V2_SUPERVISOR_STARTUP_TIMEOUT_SECONDS:-25}"

mkdir -p "${REPO_ROOT}/logs" "${REPO_ROOT}/state"

is_running() {
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

heartbeat_summary() {
  if [[ ! -f "${HEARTBEAT_FILE}" ]]; then
    return 1
  fi
  python3 - "${HEARTBEAT_FILE}" <<'PY'
import json
import sys
from datetime import datetime, timezone
path = sys.argv[1]
try:
    payload = json.load(open(path))
except Exception:
    sys.exit(1)
updated_at = payload.get("updated_at")
age_seconds = None
if updated_at:
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        age_seconds = None
parts = [
    f"heartbeat={payload.get('status', 'unknown')}",
    f"iter={payload.get('iteration', 0)}",
]
phase = payload.get("phase")
if phase:
    parts.append(f"phase={phase}")
current_window = payload.get("current_window")
if current_window:
    parts.append(f"window={current_window}")
elapsed_seconds = payload.get("elapsed_seconds")
timeout_seconds = payload.get("timeout_seconds")
if elapsed_seconds is not None and timeout_seconds:
    try:
        parts.append(f"wait={int(elapsed_seconds)}/{int(timeout_seconds)}s")
    except Exception:
        pass
repair_attempt = payload.get("repair_attempt")
if repair_attempt is not None:
    parts.append(f"repair={repair_attempt}")
if age_seconds is not None:
    parts.append(f"age={age_seconds}s")
print(" ".join(parts))
PY
}

list_running_pids() {
  pgrep -f "scripts/research_macd_aggressive_v2.py" || true
}

kill_pid_or_group() {
  local signal="$1"
  local pid="$2"
  local pgid=""
  pgid="$(ps -o pgid= -p "${pid}" 2>/dev/null | tr -d ' ' || true)"
  if [[ -n "${pgid}" ]] && [[ "${pgid}" == "${pid}" ]]; then
    kill -"${signal}" -- "-${pid}" 2>/dev/null || kill -"${signal}" "${pid}" 2>/dev/null || true
    return
  fi
  kill -"${signal}" "${pid}" 2>/dev/null || true
}

cmd_start() {
  if is_running; then
    echo "already running: pid=$(cat "${PID_FILE}")"
    exit 0
  fi
  local existing_pids
  existing_pids="$(list_running_pids | xargs echo)"
  if [[ -n "${existing_pids}" ]]; then
    echo "existing unmanaged v2 process detected: ${existing_pids}" >&2
    echo "run '$0 stop' first" >&2
    exit 1
  fi
  if [[ ! -f "${LOCAL_ENV}" ]]; then
    echo "missing env file: ${LOCAL_ENV}" >&2
    exit 1
  fi

  rm -f "${STOP_FILE}"
  local pid
  pid="$(bash -lc "cd '${REPO_ROOT}' && exec setsid nohup '${RUNNER}' > /dev/null 2>&1 < /dev/null & printf '%s\n' \$!")"
  local observed_pid=""
  for _ in $(seq 1 "${STARTUP_TIMEOUT_SECONDS}"); do
    if [[ -f "${PID_FILE}" ]]; then
      observed_pid="$(cat "${PID_FILE}")"
      if [[ -n "${observed_pid}" ]] && kill -0 "${observed_pid}" 2>/dev/null; then
        echo "started: pid=${observed_pid}"
        exit 0
      fi
    fi
    if kill -0 "${pid}" 2>/dev/null; then
      observed_pid="${pid}"
    fi
    sleep 1
  done
  echo "failed to start, check ${OUT_FILE}" >&2
  exit 1
}

cmd_stop() {
  if ! is_running; then
    local stray_pids
    stray_pids="$(list_running_pids | xargs echo)"
    if [[ -z "${stray_pids}" ]]; then
      rm -f "${PID_FILE}"
      echo "not running"
      exit 0
    fi
    for pid in ${stray_pids}; do
      kill_pid_or_group TERM "${pid}"
    done
    rm -f "${PID_FILE}"
    echo "stopped stray pids: ${stray_pids}"
    exit 0
  fi
  local pid
  pid="$(cat "${PID_FILE}")"
  touch "${STOP_FILE}"
  kill_pid_or_group TERM "${pid}"
  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${PID_FILE}" "${STOP_FILE}"
      echo "stopped: pid=${pid}"
      exit 0
    fi
    sleep 1
  done
  kill_pid_or_group KILL "${pid}"
  rm -f "${PID_FILE}" "${STOP_FILE}"
  echo "killed: pid=${pid}"
}

cmd_status() {
  if is_running; then
    local summary=""
    if summary="$(heartbeat_summary 2>/dev/null)"; then
      echo "running: pid=$(cat "${PID_FILE}") ${summary}"
    else
      echo "running: pid=$(cat "${PID_FILE}")"
    fi
  else
    local stray_pids
    stray_pids="$(list_running_pids | xargs echo)"
    if [[ -n "${stray_pids}" ]]; then
      echo "running without pidfile: ${stray_pids}"
      exit 0
    fi
    echo "not running"
    exit 1
  fi
}

case "${1:-status}" in
  start)
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  restart)
    bash "$0" stop || true
    bash "$0" start
    ;;
  reset)
    bash "${SCRIPT_DIR}/reset_research_macd_aggressive_v2_state.sh"
    ;;
  status)
    cmd_status
    ;;
  *)
    echo "usage: $0 {start|stop|restart|reset|status}" >&2
    exit 1
    ;;
esac
