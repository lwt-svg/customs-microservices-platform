#!/usr/bin/env bash
# Show only local learning environment state. This script does not change it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

ensure_runtime_directories
require_command lsof

printf 'Local learning environment\n'
printf '%-24s %-8s %s\n' "SERVICE" "STATE" "DETAIL"
print_process_status "link-booking-service" 'uvicorn.*app\.main:app' 8000
print_process_status "load-planning-service" 'uvicorn.*app\.main:app' 8200
print_background_process_status "link-rpa-worker" 'app\.rpa\.browser_worker.*--domain LINK'
print_process_status "platform-bff" 'maven|spring-boot:run|platform-bff' 8080
print_process_status "platform-web" 'node.*vite' 5173
print_process_status "link-carrier-simulator" 'uvicorn.*app\.main:app' 8100
print_process_status "carrier-simulator-web" 'node.*vite' 5174

if command -v docker >/dev/null 2>&1; then
  printf '\nDocker infrastructure\n'
  if [[ -f "${ENV_FILE}" ]]; then
    (
      cd "${REPO_ROOT}"
      # Docker Compose only prints container status; it never prints env values.
      CARRIER_CALLBACK_TOKEN="${CARRIER_CALLBACK_TOKEN:-LOCAL-DEMO-CALLBACK-TOKEN}" \
        docker compose --env-file "${ENV_FILE}" ps mysql nacos
    ) || warn "无法读取 Docker 容器状态。"
  else
    printf '%s\n' "找不到 .env.local，未查询 Docker Compose 状态。"
  fi
fi

printf '\n当前日志：%s\n' "${LOG_DIR}"
printf '有限归档：%s（每项默认最近 %s 份）\n' \
  "${LOG_ARCHIVE_DIR}" \
  "$(log_archive_keep_count)"
