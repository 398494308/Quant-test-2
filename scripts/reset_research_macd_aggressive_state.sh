#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ARCHIVE_ROOT="${REPO_ROOT}/backups/research_macd_aggressive_resets"

LOG_FILE="${REPO_ROOT}/logs/macd_aggressive_research.log"
OUT_FILE="${REPO_ROOT}/logs/macd_aggressive_research.out"
MEMORY_FILE="${REPO_ROOT}/state/optimizer_memory_macd_aggressive.json"
HEARTBEAT_FILE="${REPO_ROOT}/state/research_macd_aggressive_heartbeat.json"
PID_FILE="${REPO_ROOT}/state/research_macd_aggressive.pid"
LOCK_FILE="${REPO_ROOT}/state/research_macd_aggressive.lock"
STOP_FILE="${REPO_ROOT}/state/research_macd_aggressive.stop"
STRATEGY_BACKUP_FILE="${REPO_ROOT}/backups/strategy_macd_aggressive_backup.py"
BACKTEST_BACKUP_FILE="${REPO_ROOT}/backups/backtest_macd_aggressive_backup.py"

mkdir -p "${ARCHIVE_ROOT}" "${REPO_ROOT}/logs" "${REPO_ROOT}/state" "${REPO_ROOT}/backups"

bash "${SCRIPT_DIR}/manage_research_macd_aggressive.sh" stop >/dev/null 2>&1 || true

timestamp="$(date '+%Y%m%d_%H%M%S')"
archive_dir="${ARCHIVE_ROOT}/${timestamp}"
mkdir -p "${archive_dir}"

move_if_exists() {
  local path="$1"
  if [[ -e "${path}" ]]; then
    mv "${path}" "${archive_dir}/"
  fi
}

move_if_exists "${LOG_FILE}"
move_if_exists "${OUT_FILE}"
move_if_exists "${MEMORY_FILE}"
move_if_exists "${HEARTBEAT_FILE}"
move_if_exists "${STRATEGY_BACKUP_FILE}"
move_if_exists "${BACKTEST_BACKUP_FILE}"

rm -f "${PID_FILE}" "${LOCK_FILE}" "${STOP_FILE}"
printf '{\n  "accepted": [],\n  "rejected": []\n}\n' > "${MEMORY_FILE}"

echo "reset complete: ${archive_dir}"
