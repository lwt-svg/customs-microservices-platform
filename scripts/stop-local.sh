#!/usr/bin/env bash
# Stop only application processes recorded by these scripts.
# MySQL and Nacos stay running because they may predate this script or be used
# by another learning session. Stop them manually with Docker Compose if wanted.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

ensure_runtime_directories

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
用法：
  ./scripts/stop-local.sh
  ./scripts/stop-local.sh --manifest runtime/sessions/某次会话.tsv

不带参数时停止所有由本仓库一键脚本记录的应用进程。
带 --manifest 时，只停止该次前台会话实际新建且 PID 仍匹配的进程。
MySQL 与 Nacos 始终保留。
EOF
  exit 0
fi

if [[ "${1:-}" == "--manifest" ]]; then
  manifest_path="${2:-}"
  [[ -n "${manifest_path}" ]] || die "--manifest 后必须提供会话清单路径。"
  [[ -f "${manifest_path}" ]] || die "找不到会话清单：${manifest_path}"
  [[ $# -eq 2 ]] || die "--manifest 只接受一个会话清单路径。"

  session_services=()
  session_pids=()
  while IFS=$'\t' read -r service_name service_pid extra_field; do
    [[ -n "${service_name}" && -n "${service_pid}" ]] || continue
    [[ -z "${extra_field:-}" ]] || die "会话清单格式不正确：${manifest_path}"
    [[ "${service_pid}" =~ ^[0-9]+$ ]] || die "会话清单包含无效 PID。"
    managed_process_pattern "${service_name}" >/dev/null \
      || die "会话清单包含未知服务：${service_name}"
    session_services[${#session_services[@]}]="${service_name}"
    session_pids[${#session_pids[@]}]="${service_pid}"
  done < "${manifest_path}"

  session_count="${#session_services[@]}"
  if (( session_count == 0 )); then
    info "本次会话没有新建应用进程；复用的外部或既有进程保持运行。"
    exit 0
  fi

  for ((index = session_count - 1; index >= 0; index -= 1)); do
    stop_managed_process_if_pid "${session_services[index]}" "${session_pids[index]}"
  done
  info "本次前台会话启动的应用进程已处理。MySQL 与 Nacos 未停止。"
  exit 0
fi

[[ $# -eq 0 ]] || die "未知参数。运行 ./scripts/stop-local.sh --help 查看用法。"

stop_managed_process "carrier-simulator-web" 'node.*vite'
stop_managed_process "link-carrier-simulator" 'uvicorn.*app\.main:app'
stop_managed_process "platform-web" 'node.*vite'
stop_managed_process "platform-bff" 'maven|spring-boot:run|platform-bff'
stop_managed_process "link-rpa-worker" 'app\.rpa\.browser_worker.*--domain LINK'
stop_managed_process "load-planning-service" 'uvicorn.*app\.main:app'
stop_managed_process "link-booking-service" 'uvicorn.*app\.main:app'

info "应用进程停止完成。MySQL 与 Nacos 未停止。"
