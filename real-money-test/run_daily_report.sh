#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${SCRIPT_DIR}/runtime"

mkdir -p "${RUNTIME_DIR}"

cd "${SCRIPT_DIR}/.."
python3 "${SCRIPT_DIR}/daily_report.py" >>"${RUNTIME_DIR}/daily-report.log" 2>&1
