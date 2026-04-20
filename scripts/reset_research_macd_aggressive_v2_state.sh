#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ARCHIVE_ROOT="${REPO_ROOT}/backups/research_macd_aggressive_v2_resets"

LOG_FILE="${REPO_ROOT}/logs/macd_aggressive_research_v2.log"
OUT_FILE="${REPO_ROOT}/logs/macd_aggressive_research_v2.out"
JOURNAL_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2_journal.jsonl"
MEMORY_DIR="${REPO_ROOT}/state/research_macd_aggressive_v2_memory"
HEARTBEAT_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2_heartbeat.json"
BEST_STATE_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2_best.json"
BEST_STRATEGY_FILE="${REPO_ROOT}/backups/strategy_macd_aggressive_v2_best.py"
CANDIDATE_BACKUP_FILE="${REPO_ROOT}/backups/strategy_macd_aggressive_v2_candidate.py"
PID_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.pid"
LOCK_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.lock"
STOP_FILE="${REPO_ROOT}/state/research_macd_aggressive_v2.stop"

mkdir -p "${ARCHIVE_ROOT}" "${REPO_ROOT}/logs" "${REPO_ROOT}/state" "${REPO_ROOT}/backups"

bash "${SCRIPT_DIR}/manage_research_macd_aggressive_v2.sh" stop >/dev/null 2>&1 || true

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
move_if_exists "${JOURNAL_FILE}"
move_if_exists "${MEMORY_DIR}"
move_if_exists "${HEARTBEAT_FILE}"
move_if_exists "${BEST_STATE_FILE}"
move_if_exists "${BEST_STRATEGY_FILE}"
move_if_exists "${CANDIDATE_BACKUP_FILE}"

rm -f "${PID_FILE}" "${LOCK_FILE}" "${STOP_FILE}"

echo "reset complete: ${archive_dir}"
