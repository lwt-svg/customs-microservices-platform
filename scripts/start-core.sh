#!/usr/bin/env bash
# Start the ordinary LINK learning path:
# MySQL + Nacos + LINK Python (8000) + intelligent loading Python (8200)
# + Java BFF (8080) + platform web (5173).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

usage() {
  cat <<'EOF'
用法：
  ./scripts/start-core.sh
  ./scripts/start-core.sh --with-rules
  ./scripts/start-core.sh --with-codex
  ./scripts/start-core.sh --with-openai

默认启动核心环境且不调用模型。--with-rules 开启不联网的本地规则解读；
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
    die "未知参数：${1}。运行 ./scripts/start-core.sh --help 查看用法。"
    ;;
esac
[[ $# -le 1 ]] || die "参数过多。运行 ./scripts/start-core.sh --help 查看用法。"

ensure_runtime_directories
acquire_local_lifecycle_lock \
  || die "无法取得本地启动锁；未启动 core 环境。"
trap 'release_local_lifecycle_lock || true' EXIT
check_core_dependencies
load_local_environment
configure_load_planning_insight_mode "${insight_mode}"
configure_link_mode core
start_infrastructure
create_learning_schemas
start_link_service
record_link_mode core
start_load_planning_service
start_java_bff
verify_load_planning_bff_chain
start_platform_web

printf '\n'
info "核心学习环境已就绪："
info "  平台网站：http://127.0.0.1:5173"
info "  Java BFF：http://127.0.0.1:8080/actuator/health"
info "  LINK API：http://127.0.0.1:8000/docs"
info "  智能装载 API：http://127.0.0.1:8200/docs"
print_load_planning_insight_status
info "查看状态：./scripts/status-local.sh"
