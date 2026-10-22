#!/usr/bin/env bash
# Start the core learning path plus the optional asynchronous carrier exercise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

usage() {
  cat <<'EOF'
用法：
  ./scripts/start-full.sh
  ./scripts/start-full.sh --with-rules
  ./scripts/start-full.sh --with-codex
  ./scripts/start-full.sh --with-openai

默认启动完整异步环境且不调用模型。--with-rules 开启不联网的本地规则解读；
--with-codex 开启本机 Codex 解读，第一次使用前需要执行一次 `codex login`；
--with-openai 使用 .env.local 中的服务端 Key。
EOF
}

insight_mode="configured"
case "${1:-}" in
  "")
    ;;
  --with-rules)
    insight_mode="rules"
    ;;
  --with-codex)
    insight_mode="codex"
    ;;
  --with-openai)
    insight_mode="openai"
    ;;
  --help | -h)
    usage
    exit 0
    ;;
  *)
    die "未知参数：${1}。运行 ./scripts/start-full.sh --help 查看用法。"
    ;;
esac
[[ $# -le 1 ]] || die "参数过多。运行 ./scripts/start-full.sh --help 查看用法。"

ensure_runtime_directories
acquire_local_lifecycle_lock \
  || die "无法取得本地启动锁；未启动 full 环境。"
trap 'release_local_lifecycle_lock || true' EXIT
check_full_dependencies
load_local_environment
configure_load_planning_insight_mode "${insight_mode}"
configure_link_mode full
start_infrastructure
create_learning_schemas
start_carrier_simulator
start_link_service
record_link_mode full
start_load_planning_service
start_link_rpa_worker
start_java_bff
verify_core_bff_chains
start_platform_web
start_carrier_portal

printf '\n'
info "完整学习环境已就绪："
info "  平台网站：http://127.0.0.1:5173"
info "  模拟承运人门户：http://127.0.0.1:5174"
info "  模拟承运人 API：http://127.0.0.1:8100/docs"
info "  智能装载 API：http://127.0.0.1:8200/docs"
print_load_planning_insight_status
info "查看状态：./scripts/status-local.sh"
