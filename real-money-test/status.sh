#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${SCRIPT_DIR}/runtime/config.runtime.json"

ps -eo pid=,args= | grep -F -- "${CONFIG_PATH}" | grep "[f]reqtrade trade" || true
