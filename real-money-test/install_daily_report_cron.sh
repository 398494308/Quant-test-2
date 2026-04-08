#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CRON_CMD="cd ${REPO_ROOT} && bash ${SCRIPT_DIR}/run_daily_report.sh"
BEGIN_MARKER="# BEGIN test2-dryrun-daily-report"
END_MARKER="# END test2-dryrun-daily-report"

existing_crontab="$(crontab -l 2>/dev/null || true)"
filtered_crontab="$(
  printf '%s\n' "${existing_crontab}" | awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  '
)"

new_block="$(
  cat <<EOF
${BEGIN_MARKER}
CRON_TZ=Asia/Shanghai
0 8 * * * ${CRON_CMD}
${END_MARKER}
EOF
)"

{
  if [[ -n "${filtered_crontab}" ]]; then
    printf '%s\n' "${filtered_crontab}"
  fi
  printf '%s\n' "${new_block}"
} | crontab -

echo "installed daily report cron at 08:00 Asia/Shanghai"
