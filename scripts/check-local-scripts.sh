#!/usr/bin/env bash
# Offline/static checks for local lifecycle and E2E scripts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

shell_scripts=(
  "${SCRIPT_DIR}/lib-local.sh"
  "${SCRIPT_DIR}/start-core.sh"
  "${SCRIPT_DIR}/start-full.sh"
  "${SCRIPT_DIR}/run-local.sh"
  "${SCRIPT_DIR}/status-local.sh"
  "${SCRIPT_DIR}/stop-local.sh"
  "${SCRIPT_DIR}/test-log-rotation.sh"
  "${SCRIPT_DIR}/test-insight-modes.sh"
  "${SCRIPT_DIR}/check-local-scripts.sh"
)

for script_path in "${shell_scripts[@]}"; do
  bash -n "${script_path}"
done

python3 -c \
  'import pathlib, sys; path = pathlib.Path(sys.argv[1]); compile(path.read_text(encoding="utf-8"), str(path), "exec")' \
  "${SCRIPT_DIR}/load-planning-e2e.py"
python3 "${SCRIPT_DIR}/load-planning-e2e.py" --self-test
"${SCRIPT_DIR}/test-log-rotation.sh"
"${SCRIPT_DIR}/test-insight-modes.sh"
core_help="$("${SCRIPT_DIR}/start-core.sh" --help)"
full_help="$("${SCRIPT_DIR}/start-full.sh" --help)"
run_help="$("${SCRIPT_DIR}/run-local.sh" --help)"
for help_text in "${core_help}" "${full_help}" "${run_help}"; do
  [[ "${help_text}" == *"--with-rules"* ]] \
    || {
      printf '%s\n' "[scripts] FAIL：帮助信息缺少 --with-rules。" >&2
      exit 1
    }
  [[ "${help_text}" == *"--with-codex"* ]] \
    || {
      printf '%s\n' "[scripts] FAIL：帮助信息缺少 --with-codex。" >&2
      exit 1
    }
  [[ "${help_text}" == *"--with-openai"* ]] \
    || {
      printf '%s\n' "[scripts] FAIL：帮助信息缺少 --with-openai。" >&2
      exit 1
    }
done
if "${SCRIPT_DIR}/run-local.sh" \
  core --with-rules --with-openai >/dev/null 2>&1; then
  printf '%s\n' "[scripts] FAIL：三种显式解读模式必须互斥。" >&2
  exit 1
fi
"${SCRIPT_DIR}/stop-local.sh" --help >/dev/null

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "${shell_scripts[@]}"
else
  printf '%s\n' "[scripts] shellcheck 未安装，已跳过可选检查。"
fi

printf '%s\n' "[scripts] PASS：Bash/Python 语法、幂等重试、日志轮转、解读模式和帮助入口均通过。"
printf '%s\n' "[scripts] 仓库：${REPO_ROOT}"
