#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_FREQTRADE_BIN="${REPO_ROOT}/../test1/freqtrade_env/bin/freqtrade"

if [[ -x "${DEFAULT_FREQTRADE_BIN}" ]]; then
  FREQTRADE_BIN="${FREQTRADE_BIN:-${DEFAULT_FREQTRADE_BIN}}"
else
  FREQTRADE_BIN="${FREQTRADE_BIN:-$(command -v freqtrade || true)}"
fi

if [[ -z "${FREQTRADE_BIN}" ]]; then
  echo "freqtrade binary not found"
  exit 1
fi

python3 "${SCRIPT_DIR}/build_runtime_config.py" --mode dry-run >/dev/null

exec "${FREQTRADE_BIN}" trade \
  --config "${SCRIPT_DIR}/runtime/config.runtime.json" \
  --strategy-path "${SCRIPT_DIR}/strategies" \
  --strategy MacdAggressiveStrategy \
  --user-data-dir "${SCRIPT_DIR}/user_data" \
  --logfile "${SCRIPT_DIR}/runtime/freqtrade-dryrun.log"
