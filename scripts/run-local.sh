#!/usr/bin/env bash
# Start a local mode, keep the terminal attached, and clean up only processes
# created by this foreground session when interrupted.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

usage() {
  cat <<'EOF'
用法：
  ./scripts/run-local.sh core
  ./scripts/run-local.sh core --with-rules
  ./scripts/run-local.sh core --with-codex
  ./scripts/run-local.sh core --with-openai
  ./scripts/run-local.sh full
  ./scripts/run-local.sh full --with-rules
  ./scripts/run-local.sh full --with-codex
  ./scripts/run-local.sh full --with-openai

core：日常平台、LINK 七步和智能装载学习环境。
full：在 core 基础上增加异步模拟承运人服务与门户。
--with-rules：开启不联网、无费用的本地规则解读。
--with-codex：为候选装载方案开启本机 Codex 智能解读；第一次使用前运行 codex login。
--with-openai：使用服务端 Responses API；Key 只写在未提交的 .env.local。

脚本会保持在前台。按 Ctrl+C 后，只停止本次命令实际新建的应用进程；
启动前已经存在并被复用的服务不会被停止，MySQL 与 Nacos 也会保留。
EOF
}

mode=""
insight_mode="configured"
for argument in "$@"; do
  case "${argument}" in
    core | full)
      [[ -z "${mode}" ]] || die "只能选择一个模式：core 或 full。"
      mode="${argument}"
      ;;
    --with-rules)
      [[ "${insight_mode}" == "configured" ]] \
        || die "--with-rules、--with-codex 和 --with-openai 只能选择一个。"
      insight_mode="rules"
      ;;
    --with-codex)
      [[ "${insight_mode}" == "configured" ]] \
        || die "--with-rules、--with-codex 和 --with-openai 只能选择一个。"
      insight_mode="codex"
      ;;
    --with-openai)
      [[ "${insight_mode}" == "configured" ]] \
        || die "--with-rules、--with-codex 和 --with-openai 只能选择一个。"
      insight_mode="openai"
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *)
      die "未知参数：${argument}。运行 ./scripts/run-local.sh --help 查看用法。"
      ;;
  esac
done
mode="${mode:-core}"
if (( $# == 0 )); then
  usage
  printf '\n'
  info "未指定模式，按默认 core 启动。"
fi

ensure_runtime_directories
select_python
session_directory="${RUNTIME_DIR}/sessions"
mkdir -p "${session_directory}"
# BSD/macOS mktemp 只替换模板末尾连续的 X；把 ".tsv" 放在 X 后面会创建字面量
# run-local-core.XXXXXX.tsv，第二次启动就报 File exists。扩展名对内部清单没有意义，
# 因此使用跨 macOS/Linux 都稳定的末尾 X 模板。
session_manifest="$(mktemp "${session_directory}/run-local-${mode}.XXXXXX")"
chmod 600 "${session_manifest}"
export LOCAL_PROCESS_MANIFEST="${session_manifest}"

cleanup_session() {
  local exit_code=$?
  trap - EXIT INT TERM HUP

  printf '\n'
  info "正在结束前台监督，并清理本次会话新建的应用进程。"
  if ! "${SCRIPT_DIR}/stop-local.sh" --manifest "${session_manifest}"; then
    warn "自动清理未完整成功；可运行 ./scripts/status-local.sh 再确认。"
  fi
  rm -f "${session_manifest}"
  exit "${exit_code}"
}

trap cleanup_session EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

start_arguments=()
case "${insight_mode}" in
  rules) start_arguments+=(--with-rules) ;;
  codex) start_arguments+=(--with-codex) ;;
  openai) start_arguments+=(--with-openai) ;;
esac
"${SCRIPT_DIR}/start-${mode}.sh" "${start_arguments[@]}"

if [[ -s "${session_manifest}" ]]; then
  info "前台监督已接管本次新建的应用进程。按 Ctrl+C 安全结束。"
else
  info "本次全部复用了既有服务；按 Ctrl+C 只结束监督，不会停止这些服务。"
fi

chain_failure_count=0
monitor_tick=0
while true; do
  while IFS=$'\t' read -r service_name service_pid extra_field; do
    [[ -n "${service_name}" && -n "${service_pid}" ]] || continue
    if [[ -n "${extra_field:-}" ]] \
      || ! kill -0 "${service_pid}" >/dev/null 2>&1 \
      || ! process_command_matches \
        "${service_pid}" "$(managed_process_pattern "${service_name}")"; then
      warn "${service_name}（PID ${service_pid}）已退出或身份不再匹配。"
      warn "前台监督将结束；请查看 runtime/logs/ 下对应日志。"
      exit 1
    fi
  done < "${session_manifest}"

  # Every 10 seconds, verify the business path rather than only checking PIDs.
  # Three consecutive failures avoid tearing down the session for one brief
  # Nacos heartbeat or database connection fluctuation.
  if (( monitor_tick % 5 == 0 )); then
    if "${PYTHON_BIN}" "${SCRIPT_DIR}/load-planning-e2e.py" \
      --probe-only --wait-seconds 0 --quiet; then
      chain_failure_count=0
    else
      ((chain_failure_count += 1))
      warn "智能装载真实调用链暂时不可用（连续 ${chain_failure_count}/3 次）。"
      if (( chain_failure_count >= 3 )); then
        warn "Java BFF → Nacos → Python → MySQL 连续失败，结束本次监督。"
        exit 1
      fi
    fi
  fi

  sleep 2
  ((monitor_tick += 1))
done
