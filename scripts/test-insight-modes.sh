#!/usr/bin/env bash
# Offline self-test for plan-insight startup modes and API Key isolation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

test_directory="$(mktemp -d "${TMPDIR:-/tmp}/customs-insight-modes.XXXXXX")"

cleanup() {
  rm -rf "${test_directory}"
}
trap cleanup EXIT INT TERM HUP

PID_DIR="${test_directory}/pids"
CHECKPOINT_DIR="${test_directory}/checkpoints"
mkdir -p "${PID_DIR}" "${CHECKPOINT_DIR}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  die "解读模式离线测试需要 python3。"
fi

# 使用临时 dotenv 验证隔离，不读取仓库里的真实 .env.local，也不打印测试 Key。
test_env_file="${test_directory}/test.env"
printf '%s\n' \
  'MYSQL_ROOT_PASSWORD=test-root-password' \
  'MYSQL_DATABASE=test_database' \
  'MYSQL_USER=test_user' \
  'MYSQL_PASSWORD=test_password' \
  'NACOS_AUTH_TOKEN=test-nacos-token' \
  'OPENAI_API_KEY=test-openai-key' \
  > "${test_env_file}"

(
  ENV_FILE="${test_env_file}"
  port_is_listening() {
    return 1
  }
  load_local_environment

  [[ "${LOCAL_LOAD_PLANNING_OPENAI_API_KEY}" == "test-openai-key" ]] \
    || die "OpenAI Key 没有被保存到仅供智能装载启动使用的 Bash 变量。"
  [[ -z "${OPENAI_API_KEY+x}" ]] \
    || die "读取 dotenv 后必须立刻从公共启动环境移除 OPENAI_API_KEY。"
  if env | grep -q '^LOCAL_LOAD_PLANNING_OPENAI_API_KEY='; then
    die "保存 Key 的 Bash 变量不能被导出给普通子进程。"
  fi

  configure_load_planning_insight_mode rules
  [[ "${LOAD_PLANNING_PLAN_INSIGHTS_ENABLED}" == "true" ]] \
    || die "--with-rules 应开启方案解读。"
  [[ "${LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER}" == "rule_based" ]] \
    || die "--with-rules 应选择 rule_based Provider。"

  require_command() {
    :
  }
  codex_login_is_ready() {
    return 0
  }
  configure_load_planning_insight_mode codex
  [[ "${LOAD_PLANNING_PLAN_INSIGHTS_ENABLED}" == "true" ]] \
    || die "--with-codex 应开启方案解读。"
  [[ "${LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER}" == "codex_cli" ]] \
    || die "--with-codex 应选择 codex_cli Provider。"
)

# 即使 mode 文件仍是 OpenAI，同一条显式 --with-openai 也必须重启脚本管理的
# 8200 服务；否则修改后的 Key 或模型名称不会进入旧进程。
(
  printf '%s\n' "true:openai_responses" \
    > "${PID_DIR}/load-planning-service.insight-mode"
  LOAD_PLANNING_PLAN_INSIGHTS_ENABLED=true
  LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER=openai_responses
  LOCAL_LOAD_PLANNING_OPENAI_API_KEY="test-openai-key"
  export -n LOCAL_LOAD_PLANNING_OPENAI_API_KEY 2>/dev/null || true
  stop_call_count=0

  read_managed_pid() {
    printf '%s\n' "4242"
  }
  stop_managed_process() {
    [[ "$1" == "load-planning-service" ]] \
      || die "OpenAI 重启不应停止其他服务。"
    ((stop_call_count += 1))
  }

  configure_load_planning_insight_mode openai
  [[ "${stop_call_count}" == "1" ]] \
    || die "显式 --with-openai 应重启脚本管理的智能装载服务。"
)

# Key 只在创建 load-planning Python 子进程的瞬间导出；函数返回后必须再次消失，
# 这样随后启动的 Java BFF 和 Vite 都无法继承它。
(
  LOCAL_LOAD_PLANNING_OPENAI_API_KEY="test-openai-key"
  export -n LOCAL_LOAD_PLANNING_OPENAI_API_KEY 2>/dev/null || true
  unset OPENAI_API_KEY
  launch_was_checked=false

  assert_port_available_or_healthy() {
    return 0
  }
  run_alembic_migration() {
    :
  }
  start_managed_process() {
    [[ "$1" == "load-planning-service" ]] \
      || die "Key 注入测试启动了错误的服务。"
    [[ "${OPENAI_API_KEY:-}" == "test-openai-key" ]] \
      || die "load-planning Python 启动时没有收到 OpenAI Key。"
    env | grep -q '^OPENAI_API_KEY=test-openai-key$' \
      || die "OpenAI Key 必须作为服务端环境变量传入 load-planning Python。"
    launch_was_checked=true
  }
  wait_for_http() {
    :
  }
  record_load_planning_insight_mode() {
    :
  }

  start_load_planning_service
  [[ "${launch_was_checked}" == "true" ]] \
    || die "没有执行 load-planning Key 注入检查。"
  [[ -z "${OPENAI_API_KEY+x}" ]] \
    || die "load-planning 启动后必须移除父脚本中的 OPENAI_API_KEY。"
  if env | grep -q '^OPENAI_API_KEY='; then
    die "后续 Java/Vue/LINK 子进程不应继承 OPENAI_API_KEY。"
  fi
)

printf '%s\n' "[scripts] PASS：三种智能解读模式、OpenAI 配置重载和 API Key 隔离均通过。"
