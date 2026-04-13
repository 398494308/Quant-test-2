#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

LOCK_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.lock"
PID_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.pid"
OUT_FILE="${REPO_ROOT}/logs/macd_aggressive_research_v2.out"
STOP_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.stop"
BASE_ENV="${REPO_ROOT}/../test1/freqtrade.service.env"
LOCAL_ENV="${REPO_ROOT}/config/research_v2.env"
FALLBACK_ENV="${REPO_ROOT}/config/research.env"
RESTART_DELAY_SECONDS="${MACD_V2_SUPERVISOR_RESTART_SECONDS:-10}"

mkdir -p "${REPO_ROOT}/logs" "${REPO_ROOT}/state"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "another v2 research process is already running" >> "${OUT_FILE}"
  exit 1
fi

set -a
source "${BASE_ENV}"
source "${LOCAL_ENV}"
if [[ -f "${FALLBACK_ENV}" ]]; then
  source "${FALLBACK_ENV}"
fi
set +a

stop_requested=0
child_pid=""

forward_stop() {
  stop_requested=1
  touch "${STOP_FILE}"
  if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" 2>/dev/null; then
    kill "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
  fi
}

trap 'forward_stop; rm -f "${PID_FILE}"; exit 0' INT TERM

echo "$$" > "${PID_FILE}"
cd "${REPO_ROOT}"
rm -f "${STOP_FILE}"

while true; do
  python3 -u scripts/research_macd_aggressive_v2.py >> "${OUT_FILE}" 2>&1 &
  child_pid=$!
  set +e
  wait "${child_pid}"
  exit_code=$?
  set -e
  child_pid=""

  if [[ ${stop_requested} -eq 1 ]] || [[ -f "${STOP_FILE}" ]]; then
    break
  fi

  printf '%s supervisor observed exit code %s, restarting in %ss\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "${exit_code}" "${RESTART_DELAY_SECONDS}" >> "${OUT_FILE}"
  sleep "${RESTART_DELAY_SECONDS}"
done

rm -f "${PID_FILE}" "${STOP_FILE}"

