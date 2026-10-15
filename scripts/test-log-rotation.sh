#!/usr/bin/env bash
# Offline self-test for bounded local log rotation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-local.sh
source "${SCRIPT_DIR}/lib-local.sh"

test_directory="$(mktemp -d "${TMPDIR:-/tmp}/customs-log-rotation.XXXXXX")"
reused_pid=""
lock_holder_pid=""
lock_contender_pid=""

cleanup() {
  if [[ -n "${reused_pid}" ]]; then
    kill "${reused_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${lock_holder_pid}" ]]; then
    kill "${lock_holder_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${lock_contender_pid}" ]]; then
    kill "${lock_contender_pid}" >/dev/null 2>&1 || true
  fi
  rm -rf "${test_directory}"
}
trap cleanup EXIT INT TERM HUP

LOG_DIR="${test_directory}/logs"
LOG_ARCHIVE_DIR="${LOG_DIR}/archive"
PID_DIR="${test_directory}/pids"
CHECKPOINT_DIR="${test_directory}/checkpoints"
LOCK_DIR="${test_directory}/locks"
LIFECYCLE_LOCK_PATH="${LOCK_DIR}/local-lifecycle.lock"
LOCAL_LOG_ARCHIVE_KEEP=2
ensure_runtime_directories

# Codex 登录探测必须使用最小环境。启动脚本已经读取 .env.local 后，不能把数据库密码、
# 回调 Token 或 API Key 顺手传给诊断子进程。这里用临时假 codex 命令离线验证参数和环境。
fake_codex_bin="${test_directory}/fake-codex-bin"
mkdir -p "${fake_codex_bin}"
cat > "${fake_codex_bin}/codex" <<'FAKE_CODEX'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "login" && "${2:-}" == "status" && $# -eq 2 ]] || exit 10
for forbidden_name in \
  MYSQL_PASSWORD \
  OPENAI_API_KEY \
  CARRIER_CALLBACK_TOKEN \
  LOAD_PLANNING_DATABASE_URL; do
  [[ -z "${!forbidden_name:-}" ]] || exit 11
done
FAKE_CODEX
chmod 700 "${fake_codex_bin}/codex"
original_path="${PATH}"
PATH="${fake_codex_bin}:${PATH}"
export MYSQL_PASSWORD="must-not-reach-codex-status"
export OPENAI_API_KEY="must-not-reach-codex-status"
export CARRIER_CALLBACK_TOKEN="must-not-reach-codex-status"
export LOAD_PLANNING_DATABASE_URL="must-not-reach-codex-status"
codex_login_is_ready || die "Codex 登录探测没有使用预期的最小环境。"
PATH="${original_path}"
unset MYSQL_PASSWORD OPENAI_API_KEY CARRIER_CALLBACK_TOKEN LOAD_PLANNING_DATABASE_URL

unset LOCAL_LOG_ARCHIVE_KEEP
[[ "$(log_archive_keep_count)" == "10" ]] || die "默认归档保留数应为 10。"
LOCAL_LOG_ARCHIVE_KEEP=invalid
[[ "$(log_archive_keep_count 2>/dev/null)" == "10" ]] \
  || die "非法归档保留数应回退到默认值 10。"
LOCAL_LOG_ARCHIVE_KEEP=2

test_log="${LOG_DIR}/demo-service.log"
printf '%s\n' "first run" > "${test_log}"
rotate_current_log "${test_log}" "demo-service"
[[ ! -s "${test_log}" ]] || die "第一次轮转后 current log 没有清空。"
first_archive="$(
  find "${LOG_ARCHIVE_DIR}/demo-service" -type f -name 'demo-service.log.*.archive.*' \
    | head -n 1
)"
[[ -n "${first_archive}" ]] || die "第一次轮转没有生成归档。"
if [[ "${first_archive}" == *.gz ]]; then
  first_archive_content="$(gzip -cd "${first_archive}")"
else
  first_archive_content="$(cat "${first_archive}")"
fi
[[ "${first_archive_content}" == "first run" ]] || die "第一次归档内容与 current log 不一致。"

sleep 1
printf '%s\n' "second run" > "${test_log}"
rotate_current_log "${test_log}" "demo-service"
[[ ! -s "${test_log}" ]] || die "第二次轮转后 current log 没有清空。"

archive_count="$(
  find "${LOG_ARCHIVE_DIR}/demo-service" -type f -name 'demo-service.log.*.archive.*' \
    | wc -l | tr -d '[:space:]'
)"
[[ "${archive_count}" == "2" ]] || die "两次非空轮转应产生两份归档，实际 ${archive_count}。"

printf '%s\n' "do not delete" \
  > "${LOG_ARCHIVE_DIR}/demo-service/unrelated-troubleshooting-note.txt"
sleep 1
printf '%s\n' "third run" > "${test_log}"
rotate_current_log "${test_log}" "demo-service"
archive_count="$(
  find "${LOG_ARCHIVE_DIR}/demo-service" -type f -name 'demo-service.log.*.archive.*' \
    | wc -l | tr -d '[:space:]'
)"
[[ "${archive_count}" == "2" ]] || die "保留上限应删除最旧归档，实际 ${archive_count} 份。"

# The oldest payload must be gone while the two newest payloads remain.
retained_payloads=""
shopt -s nullglob
retained_archives=(
  "${LOG_ARCHIVE_DIR}/demo-service/demo-service.log."*.archive.*.gz
  "${LOG_ARCHIVE_DIR}/demo-service/demo-service.log."*.archive.*.log
)
shopt -u nullglob
for retained_archive in "${retained_archives[@]}"; do
  if [[ "${retained_archive}" == *.gz ]]; then
    retained_payloads="${retained_payloads}|$(gzip -cd "${retained_archive}")"
  else
    retained_payloads="${retained_payloads}|$(cat "${retained_archive}")"
  fi
done
[[ "${retained_payloads}" != *"first run"* ]] || die "最旧归档没有被删除。"
[[ "${retained_payloads}" == *"second run"* ]] || die "次新归档被错误删除。"
[[ "${retained_payloads}" == *"third run"* ]] || die "最新归档被错误删除。"
[[ "$(cat "${LOG_ARCHIVE_DIR}/demo-service/unrelated-troubleshooting-note.txt")" == "do not delete" ]] \
  || die "轮转不应删除归档目录中的无关文件。"

# Lowering retention must converge even while the current log is empty.
LOCAL_LOG_ARCHIVE_KEEP=1
rotate_current_log "${test_log}" "demo-service"
archive_count_after_empty="$(
  find "${LOG_ARCHIVE_DIR}/demo-service" -type f -name 'demo-service.log.*.archive.*' \
    | wc -l | tr -d '[:space:]'
)"
[[ "${archive_count_after_empty}" == "1" ]] \
  || die "空日志也应把归档收敛到新的保留上限。"
remaining_archive="$(
  find "${LOG_ARCHIVE_DIR}/demo-service" -type f -name 'demo-service.log.*.archive.*' \
    | head -n 1
)"
if [[ "${remaining_archive}" == *.gz ]]; then
  remaining_payload="$(gzip -cd "${remaining_archive}")"
else
  remaining_payload="$(cat "${remaining_archive}")"
fi
[[ "${remaining_payload}" == "third run" ]] || die "空日志 prune 应保留最新归档。"
[[ -f "${LOG_ARCHIVE_DIR}/demo-service/unrelated-troubleshooting-note.txt" ]] \
  || die "空日志 prune 不应删除无关文件。"

# A symlink current log or a textual path escaping LOG_DIR must be rejected
# without modifying its target.
outside_log="${test_directory}/outside.log"
printf '%s\n' "outside must survive" > "${outside_log}"
ln -s "${outside_log}" "${LOG_DIR}/symlink.log"
if rotate_current_log "${LOG_DIR}/symlink.log" "symlink-test"; then
  die "current log 是符号链接时必须拒绝轮转。"
fi
[[ "$(cat "${outside_log}")" == "outside must survive" ]] \
  || die "拒绝 symlink 时不应改动链接目标。"
if rotate_current_log "${LOG_DIR}/../escaped.log" "escape-test"; then
  die "current log 逃逸 LOG_DIR 时必须拒绝轮转。"
fi
outside_archive_directory="${test_directory}/outside-archive"
mkdir -p "${outside_archive_directory}"
ln -s "${outside_archive_directory}" "${LOG_ARCHIVE_DIR}/linked-scope"
archive_symlink_source="${LOG_DIR}/archive-symlink-source.log"
printf '%s\n' "must remain current" > "${archive_symlink_source}"
if rotate_current_log "${archive_symlink_source}" "linked-scope"; then
  die "归档作用域是符号链接时必须拒绝轮转。"
fi
[[ "$(cat "${archive_symlink_source}")" == "must remain current" ]] \
  || die "拒绝归档 symlink 时不应清空 current log。"

# A managed live process must be reused without archiving or truncating its
# current log. This protects a developer who reruns start-core/full while the
# original service is still active.
printf '%s\n' "still running" > "${test_log}"
sleep 30 &
reused_pid=$!
printf '%s\n' "${reused_pid}" > "$(pid_file_for "reuse-service")"
start_managed_process \
  "reuse-service" \
  "${test_directory}" \
  'sleep 30' \
  "${test_log}" \
  sleep 99
[[ "$(cat "${test_log}")" == "still running" ]] \
  || die "复用进程时不应清空 current log。"
reuse_archive_count="$(
  if [[ -d "${LOG_ARCHIVE_DIR}/reuse-service" ]]; then
    find "${LOG_ARCHIVE_DIR}/reuse-service" -type f | wc -l | tr -d '[:space:]'
  else
    printf '0\n'
  fi
)"
[[ "${reuse_archive_count}" == "0" ]] || die "复用进程时不应轮转日志。"

# Atomic mkdir locking serializes two independent startup processes. The
# contender must not enter until the holder explicitly releases the lock.
lock_test_directory="${test_directory}/lock-test"
mkdir -p "${lock_test_directory}"
(
  LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS=5
  acquire_local_lifecycle_lock || exit 1
  printf '%s\n' "holder acquired" > "${lock_test_directory}/holder-acquired"
  while [[ ! -f "${lock_test_directory}/allow-release" ]]; do
    sleep 1
  done
  release_local_lifecycle_lock
) &
lock_holder_pid=$!

for _ in 1 2 3 4 5; do
  [[ -f "${lock_test_directory}/holder-acquired" ]] && break
  sleep 1
done
[[ -f "${lock_test_directory}/holder-acquired" ]] || die "并发测试未取得第一把启动锁。"

(
  LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS=5
  acquire_local_lifecycle_lock || exit 1
  printf '%s\n' "contender acquired" > "${lock_test_directory}/contender-acquired"
  release_local_lifecycle_lock
) &
lock_contender_pid=$!
sleep 1
[[ ! -f "${lock_test_directory}/contender-acquired" ]] \
  || die "第二个启动流程不应在第一把锁释放前进入。"
printf '%s\n' "release" > "${lock_test_directory}/allow-release"
wait "${lock_holder_pid}"
lock_holder_pid=""
wait "${lock_contender_pid}"
lock_contender_pid=""
[[ -f "${lock_test_directory}/contender-acquired" ]] \
  || die "第一把锁释放后，等待中的启动流程应能继续。"
[[ ! -e "${LIFECYCLE_LOCK_PATH}" ]] || die "并发测试结束后启动锁应被释放。"

mkdir "${LIFECYCLE_LOCK_PATH}"
LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS=2
acquire_local_lifecycle_lock || die "空的遗留启动锁目录应可安全恢复。"
release_local_lifecycle_lock
unset LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS
[[ ! -e "${LIFECYCLE_LOCK_PATH}" ]] || die "空锁恢复测试结束后应释放启动锁。"

printf '%s\n' "[scripts] PASS：路径安全、归档新旧顺序、空日志收敛、复用边界和并发启动锁均通过。"
