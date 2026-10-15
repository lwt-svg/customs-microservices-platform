#!/usr/bin/env bash
# Shared helpers for the local learning environment scripts.
#
# These scripts intentionally manage only processes whose PIDs they wrote under
# runtime/pids/. They never inspect or print the contents of .env.local.

set -o nounset
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.local"
RUNTIME_DIR="${REPO_ROOT}/runtime"
LOG_DIR="${RUNTIME_DIR}/logs"
LOG_ARCHIVE_DIR="${LOG_DIR}/archive"
PID_DIR="${RUNTIME_DIR}/pids"
CHECKPOINT_DIR="${RUNTIME_DIR}/checkpoints"
LOCK_DIR="${RUNTIME_DIR}/locks"
LIFECYCLE_LOCK_PATH="${LOCK_DIR}/local-lifecycle.lock"
DEFAULT_LOG_ARCHIVE_KEEP=10
MAX_LOG_ARCHIVE_KEEP=100
DEFAULT_LIFECYCLE_LOCK_WAIT_SECONDS=30
MAX_LIFECYCLE_LOCK_WAIT_SECONDS=300

PYTHON_BIN=""
LOCAL_LIFECYCLE_LOCK_OWNED=false
# 只保存在当前 Bash 进程中的非导出变量。真正启动智能装载 Python 的瞬间才临时导出，
# 避免 LINK、Java BFF、Vite 或 Codex 登录诊断继承 OpenAI API Key。
LOCAL_LOAD_PLANNING_OPENAI_API_KEY=""
# 即使调用者碰巧导出了同名变量，也在当前启动脚本中取消 export 属性。
export -n LOCAL_LOAD_PLANNING_OPENAI_API_KEY 2>/dev/null || true

info() {
  printf '[local] %s\n' "$*"
}

warn() {
  printf '[local] warning: %s\n' "$*" >&2
}

die() {
  printf '[local] error: %s\n' "$*" >&2
  exit 1
}

ensure_runtime_directories() {
  local managed_directory

  for managed_directory in \
    "${LOG_DIR}" \
    "${LOG_ARCHIVE_DIR}" \
    "${PID_DIR}" \
    "${CHECKPOINT_DIR}" \
    "${LOCK_DIR}"; do
    [[ ! -L "${managed_directory}" ]] \
      || die "运行目录不能是符号链接：${managed_directory}"
  done
  mkdir -p \
    "${LOG_DIR}" \
    "${LOG_ARCHIVE_DIR}" \
    "${PID_DIR}" \
    "${CHECKPOINT_DIR}" \
    "${LOCK_DIR}"
}

log_archive_keep_count() {
  local configured_keep="${LOCAL_LOG_ARCHIVE_KEEP:-${DEFAULT_LOG_ARCHIVE_KEEP}}"
  local parsed_keep=""

  # Limit the override as well as validating it. A typo such as 999999 should
  # not silently turn local log retention back into unbounded growth.
  if [[ "${configured_keep}" =~ ^[0-9]+$ ]] \
    && (( ${#configured_keep} <= 3 )); then
    parsed_keep=$((10#${configured_keep}))
  fi
  if [[ -z "${parsed_keep}" ]] \
    || (( parsed_keep < 1 || parsed_keep > MAX_LOG_ARCHIVE_KEEP )); then
    warn "LOCAL_LOG_ARCHIVE_KEEP=${configured_keep} 无效；改用默认值 ${DEFAULT_LOG_ARCHIVE_KEEP}（允许 1-${MAX_LOG_ARCHIVE_KEEP}）。"
    parsed_keep="${DEFAULT_LOG_ARCHIVE_KEEP}"
  fi

  printf '%s\n' "${parsed_keep}"
}

canonical_directory() {
  local directory_path="$1"
  (
    cd "${directory_path}" >/dev/null 2>&1 || return 1
    pwd -P
  )
}

validate_log_roots() {
  local canonical_log_directory
  local canonical_archive_parent

  [[ "${LOG_DIR}" == /* ]] \
    || {
      warn "LOG_DIR 必须是绝对路径：${LOG_DIR}"
      return 1
    }
  [[ "${LOG_ARCHIVE_DIR}" == "${LOG_DIR%/}/archive" ]] \
    || {
      warn "LOG_ARCHIVE_DIR 必须严格位于 LOG_DIR/archive：${LOG_ARCHIVE_DIR}"
      return 1
    }
  [[ ! -L "${LOG_DIR}" && ! -L "${LOG_ARCHIVE_DIR}" ]] \
    || {
      warn "日志根目录不能是符号链接。"
      return 1
    }

  mkdir -p "${LOG_DIR}" "${LOG_ARCHIVE_DIR}" || return 1
  canonical_log_directory="$(canonical_directory "${LOG_DIR}")" || return 1
  canonical_archive_parent="$(
    canonical_directory "$(dirname "${LOG_ARCHIVE_DIR}")"
  )" || return 1
  [[ "${canonical_archive_parent}" == "${canonical_log_directory}" ]] \
    || {
      warn "日志归档目录不在预期的 LOG_DIR 下：${LOG_ARCHIVE_DIR}"
      return 1
    }
}

validate_current_log_path() {
  local log_path="$1"
  local log_basename
  local canonical_log_directory
  local canonical_log_parent

  validate_log_roots || return 1
  [[ "${log_path}" == /* ]] \
    || {
      warn "current log 必须是绝对路径：${log_path}"
      return 1
    }
  [[ ! -L "${log_path}" ]] \
    || {
      warn "current log 不能是符号链接：${log_path}"
      return 1
    }

  log_basename="$(basename "${log_path}")"
  [[ "${log_basename}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*[.]log$ ]] \
    || {
      warn "current log 文件名不安全：${log_basename}"
      return 1
    }
  [[ "${log_path}" == "${LOG_DIR%/}/${log_basename}" ]] \
    || {
      warn "current log 必须直接位于 LOG_DIR：${log_path}"
      return 1
    }

  canonical_log_directory="$(canonical_directory "${LOG_DIR}")" || return 1
  canonical_log_parent="$(
    canonical_directory "$(dirname "${log_path}")"
  )" || return 1
  [[ "${canonical_log_parent}" == "${canonical_log_directory}" ]] \
    || {
      warn "current log 解析后不在 LOG_DIR 内：${log_path}"
      return 1
    }
}

prepare_archive_directory() {
  local archive_scope="$1"
  local archive_directory="${LOG_ARCHIVE_DIR}/${archive_scope}"
  local canonical_archive_root
  local canonical_archive_directory
  local canonical_archive_parent

  [[ "${archive_scope}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || {
      warn "拒绝不安全的日志归档目录名：${archive_scope}"
      return 1
    }
  [[ ! -L "${archive_directory}" ]] \
    || {
      warn "日志归档作用域不能是符号链接：${archive_directory}"
      return 1
    }

  mkdir -p "${archive_directory}" || return 1
  [[ ! -L "${archive_directory}" ]] \
    || {
      warn "日志归档作用域被替换成符号链接：${archive_directory}"
      return 1
    }
  canonical_archive_root="$(canonical_directory "${LOG_ARCHIVE_DIR}")" || return 1
  canonical_archive_directory="$(
    canonical_directory "${archive_directory}"
  )" || return 1
  canonical_archive_parent="$(
    dirname "${canonical_archive_directory}"
  )" || return 1
  [[ "${canonical_archive_parent}" == "${canonical_archive_root}" ]] \
    || {
      warn "日志归档作用域不在 LOG_ARCHIVE_DIR 内：${archive_directory}"
      return 1
    }
}

prune_log_archives() {
  local log_path="$1"
  local archive_scope="$2"
  local keep_count
  local archive_directory="${LOG_ARCHIVE_DIR}/${archive_scope}"
  local archive_basename
  local archive_paths=()
  local delete_count
  local deleted_count=0
  local archive_path
  local ordered_archives

  validate_current_log_path "${log_path}" || return 1
  prepare_archive_directory "${archive_scope}" || return 1
  archive_basename="$(basename "${log_path}")"
  keep_count="$(log_archive_keep_count)"

  # Only files with the exact names produced by rotate_current_log participate
  # in retention. Notes or other troubleshooting files in the same directory
  # are deliberately left alone.
  shopt -s nullglob
  archive_paths=(
    "${archive_directory}/${archive_basename}."*.archive.*.gz
    "${archive_directory}/${archive_basename}."*.archive.*.log
  )
  shopt -u nullglob

  delete_count=$((${#archive_paths[@]} - keep_count))
  (( delete_count > 0 )) || return 0

  # Archive names are generated by this script and cannot contain newlines.
  # `ls -t` is available on both macOS and Linux and lets us remove only the
  # exact oldest files from this one log scope.
  # shellcheck disable=SC2012  # Generated names cannot contain newlines.
  if ! ordered_archives="$(LC_ALL=C ls -1tr "${archive_paths[@]}")"; then
    warn "无法按时间排序日志归档：${archive_directory}/${archive_basename}.*"
    return 1
  fi
  while IFS= read -r archive_path; do
    [[ -n "${archive_path}" ]] || continue
    [[ -f "${archive_path}" && ! -L "${archive_path}" ]] \
      || {
        warn "拒绝删除非普通文件或符号链接归档：${archive_path}"
        return 1
      }
    if ! rm -f "${archive_path}"; then
      warn "无法删除超出保留上限的旧日志归档：${archive_path}"
      return 1
    fi
    ((deleted_count += 1))
    (( deleted_count >= delete_count )) && break
  done <<< "${ordered_archives}"
}

rotate_current_log() {
  local log_path="$1"
  local archive_scope="$2"
  local archive_directory="${LOG_ARCHIVE_DIR}/${archive_scope}"
  local archive_basename
  local archive_timestamp
  local temporary_archive
  local final_archive

  validate_current_log_path "${log_path}" || return 1
  prepare_archive_directory "${archive_scope}" || return 1

  # Missing and empty current logs need no archive, but creating the empty
  # current file keeps tail -f predictable. Retention still runs so lowering
  # LOCAL_LOG_ARCHIVE_KEEP converges even before another non-empty run.
  if [[ ! -e "${log_path}" ]]; then
    : > "${log_path}"
    if ! prune_log_archives "${log_path}" "${archive_scope}"; then
      warn "current log 为空，但旧归档未能按保留上限收敛：${log_path}"
      return 1
    fi
    return 0
  fi
  [[ -f "${log_path}" && ! -L "${log_path}" ]] \
    || {
      warn "current log 不是普通文件，拒绝轮转：${log_path}"
      return 1
    }
  if [[ ! -s "${log_path}" ]]; then
    : > "${log_path}"
    if ! prune_log_archives "${log_path}" "${archive_scope}"; then
      warn "current log 为空，但旧归档未能按保留上限收敛：${log_path}"
      return 1
    fi
    return 0
  fi

  archive_basename="$(basename "${log_path}")"
  archive_timestamp="$(date '+%Y%m%d-%H%M%S')"
  temporary_archive="$(
    mktemp "${archive_directory}/${archive_basename}.${archive_timestamp}.archive.XXXXXX"
  )" || return 1

  # gzip is normally available on both macOS and Linux. If it is missing or
  # compression fails, preserve the log uncompressed rather than losing it.
  if command -v gzip >/dev/null 2>&1 \
    && gzip -c "${log_path}" > "${temporary_archive}"; then
    final_archive="${temporary_archive}.gz"
  else
    : > "${temporary_archive}"
    if ! cp "${log_path}" "${temporary_archive}"; then
      rm -f "${temporary_archive}"
      return 1
    fi
    final_archive="${temporary_archive}.log"
  fi
  if ! mv "${temporary_archive}" "${final_archive}"; then
    rm -f "${temporary_archive}"
    return 1
  fi

  # Truncate only after a complete archive exists. A disk or permission error
  # therefore leaves the original current log intact.
  if ! : > "${log_path}"; then
    warn "旧日志已经安全归档，但 current log 无法清空：${log_path}"
    return 1
  fi
  if ! prune_log_archives "${log_path}" "${archive_scope}"; then
    warn "旧日志已经归档并清空，但过期归档清理失败：${archive_directory}"
    return 1
  fi
  info "已归档旧日志：${final_archive}"
}

lifecycle_lock_wait_seconds() {
  local configured_wait="${LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS:-${DEFAULT_LIFECYCLE_LOCK_WAIT_SECONDS}}"
  local parsed_wait=""

  if [[ "${configured_wait}" =~ ^[0-9]+$ ]] \
    && (( ${#configured_wait} <= 3 )); then
    parsed_wait=$((10#${configured_wait}))
  fi
  if [[ -z "${parsed_wait}" ]] \
    || (( parsed_wait < 1 || parsed_wait > MAX_LIFECYCLE_LOCK_WAIT_SECONDS )); then
    warn "LOCAL_LIFECYCLE_LOCK_WAIT_SECONDS=${configured_wait} 无效；改用默认值 ${DEFAULT_LIFECYCLE_LOCK_WAIT_SECONDS}。"
    parsed_wait="${DEFAULT_LIFECYCLE_LOCK_WAIT_SECONDS}"
  fi
  printf '%s\n' "${parsed_wait}"
}

validate_lifecycle_lock_paths() {
  local canonical_lock_directory
  local canonical_lock_parent

  [[ "${LOCK_DIR}" == /* ]] \
    || {
      warn "LOCK_DIR 必须是绝对路径：${LOCK_DIR}"
      return 1
    }
  [[ "${LIFECYCLE_LOCK_PATH}" == "${LOCK_DIR%/}/local-lifecycle.lock" ]] \
    || {
      warn "启动锁路径不在预期的 LOCK_DIR 内：${LIFECYCLE_LOCK_PATH}"
      return 1
    }
  [[ ! -L "${LOCK_DIR}" && ! -L "${LIFECYCLE_LOCK_PATH}" ]] \
    || {
      warn "启动锁目录不能是符号链接。"
      return 1
    }

  mkdir -p "${LOCK_DIR}" || return 1
  canonical_lock_directory="$(canonical_directory "${LOCK_DIR}")" || return 1
  canonical_lock_parent="$(
    canonical_directory "$(dirname "${LIFECYCLE_LOCK_PATH}")"
  )" || return 1
  [[ "${canonical_lock_parent}" == "${canonical_lock_directory}" ]] \
    || {
      warn "启动锁解析后不在 LOCK_DIR 内：${LIFECYCLE_LOCK_PATH}"
      return 1
    }
}

read_lifecycle_lock_owner() {
  local owner_file="${LIFECYCLE_LOCK_PATH}/owner"
  local owner_pid=""
  local owner_token=""
  local extra_field=""

  [[ -f "${owner_file}" && ! -L "${owner_file}" ]] || return 1
  IFS=$'\t' read -r owner_pid owner_token extra_field < "${owner_file}" || return 1
  [[ "${owner_pid}" =~ ^[0-9]+$ && -n "${owner_token}" && -z "${extra_field}" ]] \
    || return 1
  printf '%s\t%s\n' "${owner_pid}" "${owner_token}"
}

acquire_local_lifecycle_lock() {
  local wait_seconds
  local waited_seconds=0
  local owner_record=""
  local owner_pid=""
  local owner_token=""
  local lock_token=""

  validate_lifecycle_lock_paths || return 1

  # If a lifecycle script ever invokes another lifecycle script while holding
  # the lock, the inherited token permits that child to re-enter without
  # deadlocking itself. Normal run-local delegates locking to its start script.
  if [[ -n "${LOCAL_LIFECYCLE_LOCK_TOKEN:-}" ]] \
    && owner_record="$(read_lifecycle_lock_owner)"; then
    IFS=$'\t' read -r owner_pid owner_token <<< "${owner_record}"
    if [[ "${owner_token}" == "${LOCAL_LIFECYCLE_LOCK_TOKEN}" ]] \
      && kill -0 "${owner_pid}" >/dev/null 2>&1; then
      return 0
    fi
  fi

  wait_seconds="$(lifecycle_lock_wait_seconds)"
  while true; do
    if mkdir "${LIFECYCLE_LOCK_PATH}" 2>/dev/null; then
      lock_token="$$-${RANDOM}-$(date '+%s')"
      if ! printf '%s\t%s\n' "$$" "${lock_token}" \
        > "${LIFECYCLE_LOCK_PATH}/owner"; then
        rmdir "${LIFECYCLE_LOCK_PATH}" 2>/dev/null || true
        warn "无法写入本地启动锁 owner 文件。"
        return 1
      fi
      export LOCAL_LIFECYCLE_LOCK_TOKEN="${lock_token}"
      LOCAL_LIFECYCLE_LOCK_OWNED=true
      return 0
    fi

    [[ ! -L "${LIFECYCLE_LOCK_PATH}" ]] \
      || {
        warn "启动锁路径被替换成符号链接：${LIFECYCLE_LOCK_PATH}"
        return 1
      }
    if owner_record="$(read_lifecycle_lock_owner)"; then
      IFS=$'\t' read -r owner_pid owner_token <<< "${owner_record}"
      if ! kill -0 "${owner_pid}" >/dev/null 2>&1; then
        rm -f "${LIFECYCLE_LOCK_PATH}/owner"
        rmdir "${LIFECYCLE_LOCK_PATH}" 2>/dev/null || true
        continue
      fi
    elif rmdir "${LIFECYCLE_LOCK_PATH}" 2>/dev/null; then
      # A process can be killed after atomic mkdir but before writing owner.
      # Only an actually empty lock directory is recovered; unknown contents
      # are never removed.
      continue
    fi

    if (( waited_seconds >= wait_seconds )); then
      warn "另一个本地启动流程仍持有锁，等待 ${wait_seconds} 秒后放弃：${LIFECYCLE_LOCK_PATH}"
      return 1
    fi
    sleep 1
    ((waited_seconds += 1))
  done
}

release_local_lifecycle_lock() {
  local owner_record=""
  local owner_pid=""
  local owner_token=""
  local release_path=""
  local release_attempt=1

  [[ "${LOCAL_LIFECYCLE_LOCK_OWNED}" == "true" ]] || return 0
  if [[ -L "${LIFECYCLE_LOCK_PATH}" ]]; then
    warn "启动锁路径已变成符号链接，拒绝清理：${LIFECYCLE_LOCK_PATH}"
    return 1
  fi
  if ! owner_record="$(read_lifecycle_lock_owner)"; then
    warn "无法确认本进程拥有启动锁，不会删除：${LIFECYCLE_LOCK_PATH}"
    return 1
  fi
  IFS=$'\t' read -r owner_pid owner_token <<< "${owner_record}"
  if [[ "${owner_pid}" != "$$" \
    || "${owner_token}" != "${LOCAL_LIFECYCLE_LOCK_TOKEN:-}" ]]; then
    warn "启动锁 owner 已变化，不会删除：${LIFECYCLE_LOCK_PATH}"
    return 1
  fi

  # 先把整个锁目录原子移出公共锁路径，再清理 owner。若先删除 owner 再 rmdir，
  # 等待者可能在两步之间把“空锁”当成遗留目录回收，造成释放方偶发失败。
  while (( release_attempt <= 10 )); do
    release_path="${LOCK_DIR}/local-lifecycle.release.$$-${RANDOM}-${release_attempt}"
    [[ -e "${release_path}" || -L "${release_path}" ]] \
      || break
    ((release_attempt += 1))
  done
  (( release_attempt <= 10 )) \
    || {
      warn "无法生成安全的启动锁释放路径。"
      return 1
    }
  if ! mv "${LIFECYCLE_LOCK_PATH}" "${release_path}"; then
    warn "无法原子释放本地启动锁：${LIFECYCLE_LOCK_PATH}"
    return 1
  fi

  LOCAL_LIFECYCLE_LOCK_OWNED=false
  unset LOCAL_LIFECYCLE_LOCK_TOKEN
  if ! rm -f "${release_path}/owner"; then
    warn "启动锁已释放，但无法清理 owner 文件：${release_path}/owner"
    return 1
  fi
  if ! rmdir "${release_path}"; then
    warn "已释放的启动锁目录包含未知内容，未强制删除：${release_path}"
    return 1
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1。请先安装后再运行。"
}

codex_login_is_ready() {
  local minimal_environment=(
    env
    -i
    "PATH=${PATH}"
    "HOME=${HOME}"
  )

  # 登录状态只需要 Codex 自己的目录和基础运行环境。不要因为启动脚本已经读取
  # .env.local，就把数据库密码、回调 Token 或 OpenAI API Key 继承给这个诊断进程。
  [[ -n "${CODEX_HOME:-}" ]] \
    && minimal_environment+=("CODEX_HOME=${CODEX_HOME}")
  [[ -n "${TMPDIR:-}" ]] \
    && minimal_environment+=("TMPDIR=${TMPDIR}")
  [[ -n "${LANG:-}" ]] \
    && minimal_environment+=("LANG=${LANG}")
  [[ -n "${LC_ALL:-}" ]] \
    && minimal_environment+=("LC_ALL=${LC_ALL}")

  "${minimal_environment[@]}" codex login status >/dev/null 2>&1
}

load_local_environment() {
  [[ -f "${ENV_FILE}" ]] || die "找不到 .env.local。请先复制 .env.example 为 .env.local，并填写本机学习环境配置。"

  # .env.local is a local developer-controlled dotenv file. Export its values
  # for Python, Java, and Docker without echoing any value to the terminal.
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a

  # .env.local 被统一读取是为了复用现有启动体系，但模型密钥不应跟着统一环境传播。
  # 同时兼容“写在 .env.local”与“启动命令前临时 export”两种本地输入方式。
  LOCAL_LOAD_PLANNING_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
  export -n LOCAL_LOAD_PLANNING_OPENAI_API_KEY 2>/dev/null || true
  unset OPENAI_API_KEY

  require_environment_value "MYSQL_ROOT_PASSWORD"
  require_environment_value "MYSQL_DATABASE"
  require_environment_value "MYSQL_USER"
  require_environment_value "MYSQL_PASSWORD"
  require_environment_value "NACOS_AUTH_TOKEN"

  # Older local .env.local files predate chapter 22. Compose parses all
  # services even when only mysql/nacos are selected, so give that unrelated
  # interpolation a safe local value without writing it into .env.local.
  if [[ -z "${CARRIER_CALLBACK_TOKEN:-}" ]]; then
    export CARRIER_CALLBACK_TOKEN="LOCAL-DEMO-CALLBACK-TOKEN"
  fi

  # Older .env.local files also predate the optional carrier simulator. Build
  # its local-only connection URL in memory from the existing MySQL account so
  # start-full works without overwriting or rewriting the user's file.
  if [[ -z "${CARRIER_DATABASE_URL:-}" ]]; then
    local encoded_mysql_user
    local encoded_mysql_password
    encoded_mysql_user="$(
      "${PYTHON_BIN}" -c \
        'import os, urllib.parse; print(urllib.parse.quote(os.environ["MYSQL_USER"], safe=""))'
    )"
    encoded_mysql_password="$(
      "${PYTHON_BIN}" -c \
        'import os, urllib.parse; print(urllib.parse.quote(os.environ["MYSQL_PASSWORD"], safe=""))'
    )"
    export CARRIER_DATABASE_URL="mysql+asyncmy://${encoded_mysql_user}:${encoded_mysql_password}@127.0.0.1:3306/link_carrier_simulator"
  fi
  if [[ -z "${CARRIER_CALLBACK_URL:-}" ]]; then
    export CARRIER_CALLBACK_URL="http://127.0.0.1:8000/api/v1/carrier-callbacks/link"
  fi

  # 第 23 章新增的智能装载服务也使用独立 schema。旧版 .env.local 不需要立刻手工补字段：
  # 这里只为本次启动进程构造本机 URL，不读取企业地址，也不会把结果写回配置文件。
  if [[ -z "${LOAD_PLANNING_DATABASE_URL:-}" ]]; then
    local encoded_load_mysql_user
    local encoded_load_mysql_password
    encoded_load_mysql_user="$(
      "${PYTHON_BIN}" -c \
        'import os, urllib.parse; print(urllib.parse.quote(os.environ["MYSQL_USER"], safe=""))'
    )"
    encoded_load_mysql_password="$(
      "${PYTHON_BIN}" -c \
        'import os, urllib.parse; print(urllib.parse.quote(os.environ["MYSQL_PASSWORD"], safe=""))'
    )"
    export LOAD_PLANNING_DATABASE_URL="mysql+asyncmy://${encoded_load_mysql_user}:${encoded_load_mysql_password}@127.0.0.1:3306/load_planning_service"
  fi
  export LOAD_PLANNING_NACOS_DISCOVERY_ENABLED="${LOAD_PLANNING_NACOS_DISCOVERY_ENABLED:-true}"
  export LOAD_PLANNING_NACOS_SERVICE_NAME="${LOAD_PLANNING_NACOS_SERVICE_NAME:-load-planning-service}"
  export LOAD_PLANNING_NACOS_REGISTER_IP="${LOAD_PLANNING_NACOS_REGISTER_IP:-127.0.0.1}"
  export LOAD_PLANNING_NACOS_REGISTER_PORT="${LOAD_PLANNING_NACOS_REGISTER_PORT:-8200}"
  # LangGraph 的 SQLite checkpoint 跟随智能装载 Python 进程自动打开，不增加任何
  # 需要手工启动的服务。旧 .env.local 没有这些字段时也直接使用本地安全默认值。
  export LOAD_PLANNING_LANGGRAPH_ENABLED="${LOAD_PLANNING_LANGGRAPH_ENABLED:-true}"
  export LOAD_PLANNING_LANGGRAPH_CHECKPOINT_PATH="${LOAD_PLANNING_LANGGRAPH_CHECKPOINT_PATH:-${CHECKPOINT_DIR}/load-planning-langgraph.sqlite3}"

  # 第 23.9 章的智能解读是可选旁路。旧 .env.local 没有这些字段时保持关闭，
  # 因而普通的一键启动不会调用 Codex/OpenAI，也不会产生模型费用。
  export LOAD_PLANNING_PLAN_INSIGHTS_ENABLED="${LOAD_PLANNING_PLAN_INSIGHTS_ENABLED:-false}"
  export LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER="${LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER:-rule_based}"
  export LOAD_PLANNING_LLM_MODEL="${LOAD_PLANNING_LLM_MODEL:-gpt-6-luna}"
  export LOAD_PLANNING_CODEX_MODEL="${LOAD_PLANNING_CODEX_MODEL:-}"
  export LOAD_PLANNING_LLM_TIMEOUT_SECONDS="${LOAD_PLANNING_LLM_TIMEOUT_SECONDS:-90}"
  export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
}

require_environment_value() {
  local variable_name="$1"
  [[ -n "${!variable_name:-}" ]] || die ".env.local 缺少 ${variable_name}。请参考 .env.example 补充本机配置。"
}

select_python() {
  local conda_python="/opt/miniconda3/envs/customs-affairs-os-lab/bin/python"

  if [[ -x "${conda_python}" ]]; then
    PYTHON_BIN="${conda_python}"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    die "没有找到 Python。请安装 Python 3.12，或创建 customs-affairs-os-lab Conda 环境。"
  fi

  "${PYTHON_BIN}" -c 'import sys; assert sys.version_info[:2] >= (3, 12)' \
    >/dev/null 2>&1 || die "需要 Python 3.12 或更高版本；当前 Python 不满足要求。"
}

check_core_dependencies() {
  require_command docker
  require_command curl
  require_command lsof
  require_command mvn
  require_command npm
  require_command java
  select_python
}

check_full_dependencies() {
  check_core_dependencies
}

port_is_listening() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local log_path="$3"
  local attempts="${4:-45}"
  local attempt=1

  while (( attempt <= attempts )); do
    if curl --fail --silent --show-error --max-time 2 "${url}" >/dev/null 2>&1; then
      info "${label} 已就绪。"
      return 0
    fi
    sleep 1
    ((attempt += 1))
  done

  die "${label} 在 ${attempts} 秒内没有就绪。请查看日志：${log_path}"
}

pid_file_for() {
  printf '%s/%s.pid\n' "${PID_DIR}" "$1"
}

read_managed_pid() {
  local service_name="$1"
  local pid_file
  pid_file="$(pid_file_for "${service_name}")"

  [[ -f "${pid_file}" ]] || return 1

  local pid
  pid="$(tr -d '[:space:]' < "${pid_file}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  printf '%s\n' "${pid}"
}

process_command_matches() {
  local pid="$1"
  local expected_pattern="$2"
  local command_line

  command_line="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
  [[ "${command_line}" =~ ${expected_pattern} ]]
}

remove_stale_pid_file() {
  local service_name="$1"
  local pid

  if pid="$(read_managed_pid "${service_name}")"; then
    return 0
  fi
  rm -f "$(pid_file_for "${service_name}")"
}

start_managed_process() {
  local service_name="$1"
  local working_directory="$2"
  local expected_pattern="$3"
  local log_path="$4"
  shift 4

  remove_stale_pid_file "${service_name}"

  local managed_pid
  if managed_pid="$(read_managed_pid "${service_name}")"; then
    if process_command_matches "${managed_pid}" "${expected_pattern}"; then
      info "${service_name} 已由本脚本启动（PID ${managed_pid}），复用该进程。"
      return 0
    fi
    warn "${service_name} 的 PID 文件与当前命令不匹配，已忽略该进程。"
    rm -f "$(pid_file_for "${service_name}")"
  fi

  rotate_current_log "${log_path}" "${service_name}" \
    || die "${service_name} 日志轮转未完成；未启动新进程。"

  (
    cd "${working_directory}"
    exec "$@"
  ) >>"${log_path}" 2>&1 &

  local child_pid=$!
  printf '%s\n' "${child_pid}" > "$(pid_file_for "${service_name}")"
  record_session_process "${service_name}" "${child_pid}"
  info "已启动 ${service_name}（PID ${child_pid}）。日志：${log_path}"
}

record_session_process() {
  local service_name="$1"
  local pid="$2"
  local manifest_path="${LOCAL_PROCESS_MANIFEST:-}"

  [[ -n "${manifest_path}" ]] || return 0
  if ! printf '%s\t%s\n' "${service_name}" "${pid}" >> "${manifest_path}"; then
    # This process was created by the current call, so stopping this exact PID
    # is safer than leaving an untracked child behind.
    kill "${pid}" >/dev/null 2>&1 || true
    rm -f "$(pid_file_for "${service_name}")"
    die "无法记录本次前台会话启动的 ${service_name}；已停止刚创建的进程。"
  fi
}

ensure_node_modules() {
  local frontend_directory="$1"
  local label="$2"

  if [[ ! -d "${frontend_directory}/node_modules" ]]; then
    info "${label} 首次安装前端依赖；这可能需要一点时间。"
    (
      cd "${frontend_directory}"
      npm install
    ) || die "${label} 的 npm install 失败。请检查网络和 package.json。"
  fi
}

start_infrastructure() {
  local compose_log="${LOG_DIR}/compose.log"

  rotate_current_log "${compose_log}" "compose" \
    || die "Docker Compose 日志轮转未完成；未继续启动基础设施。"
  info "启动或复用 MySQL 与 Nacos（Docker）。"
  (
    cd "${REPO_ROOT}"
    docker compose --env-file "${ENV_FILE}" up -d mysql nacos
  ) >>"${compose_log}" 2>&1 || die "MySQL/Nacos 启动失败。请查看日志：${compose_log}"

  wait_for_http \
    "http://127.0.0.1:8848/nacos/actuator/health" \
    "Nacos" \
    "${compose_log}" \
    90

  # The Docker health endpoint provides a better signal than only checking that
  # port 3306 was opened.
    local attempt=1
  while (( attempt <= 90 )); do
    if (
      cd "${REPO_ROOT}"
      docker compose --env-file "${ENV_FILE}" exec -T \
        -e MYSQL_PWD="${MYSQL_PASSWORD}" \
        mysql mysqladmin ping -h localhost -u"${MYSQL_USER}" --silent
    ) >/dev/null 2>&1; then
      info "MySQL 已就绪。"
      return 0
    fi
    sleep 1
    ((attempt += 1))
  done

  die "MySQL 在 90 秒内没有就绪。请查看日志：${compose_log}"
}

create_learning_schemas() {
  local schema_log="${LOG_DIR}/schema-setup.log"
  local schemas=(
    "link_booking_service"
    "platform_bff_lab"
    "link_carrier_simulator"
    "load_planning_service"
  )
  local schema

  rotate_current_log "${schema_log}" "schema-setup" \
    || die "schema 初始化日志轮转未完成；未继续创建数据库。"
  for schema in "${schemas[@]}"; do
    (
      cd "${REPO_ROOT}"
      docker compose --env-file "${ENV_FILE}" exec -T \
        -e MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" \
        mysql mysql -uroot \
        -e "CREATE DATABASE IF NOT EXISTS \`${schema}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
            GRANT ALL PRIVILEGES ON \`${schema}\`.* TO '${MYSQL_USER}'@'%';
            FLUSH PRIVILEGES"
    ) >>"${schema_log}" 2>&1 || die "创建本地 schema 失败。请查看日志：${schema_log}"
  done
  info "四个本地学习 schema 已确认存在。"
}

configure_link_mode() {
  local desired_mode="$1"
  local mode_file="${PID_DIR}/link-booking-service.mode"
  local previous_mode=""
  local managed_pid=""

  case "${desired_mode}" in
    core)
      export CARRIER_DISPATCH_ENABLED=false
      export CARRIER_DISPATCH_RETRY_ENABLED=false
      ;;
    full)
      export CARRIER_DISPATCH_ENABLED=true
      export CARRIER_DISPATCH_RETRY_ENABLED=true
      ;;
    *)
      die "未知 LINK 启动模式：${desired_mode}"
      ;;
  esac

  [[ -f "${mode_file}" ]] && previous_mode="$(tr -d '[:space:]' < "${mode_file}")"
  if managed_pid="$(read_managed_pid "link-booking-service")"; then
    if [[ -n "${previous_mode}" && "${previous_mode}" != "${desired_mode}" ]]; then
      info "LINK 将从 ${previous_mode} 模式切换到 ${desired_mode} 模式。"
      stop_managed_process "link-booking-service" 'uvicorn.*app\.main:app'
    fi
  elif port_is_listening 8000; then
    warn "8000 由外部进程运行，脚本无法切换其 core/full 配置；如需切换，请先手工重启该 LINK 进程。"
  fi
}

record_link_mode() {
  local desired_mode="$1"
  if read_managed_pid "link-booking-service" >/dev/null 2>&1; then
    printf '%s\n' "${desired_mode}" > "${PID_DIR}/link-booking-service.mode"
  fi
}

configure_load_planning_insight_mode() {
  local requested_mode="${1:-configured}"
  local mode_file="${PID_DIR}/load-planning-service.insight-mode"
  local previous_mode=""
  local managed_pid=""
  local force_managed_restart=false
  local enabled="${LOAD_PLANNING_PLAN_INSIGHTS_ENABLED:-false}"
  local provider="${LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER:-rule_based}"

  # 三个 --with-* 参数是面向学习者的短入口，只覆盖当前启动进程，
  # 不回写 .env.local。
  # 未传该参数时仍尊重 .env.local；而旧配置因为 load_local_environment 的默认值会关闭。
  case "${requested_mode}" in
    configured)
      ;;
    rules)
      enabled=true
      provider=rule_based
      ;;
    codex)
      enabled=true
      provider=codex_cli
      ;;
    openai)
      enabled=true
      provider=openai_responses
      # Key 与模型名称不会写进 mode 文件。用户显式再次选择 OpenAI 时，
      # 主动重启脚本管理的 8200，确保它重新读取本次 .env.local 配置。
      force_managed_restart=true
      ;;
    *)
      die "未知智能解读启动模式：${requested_mode}"
      ;;
  esac

  case "${enabled}" in
    true | TRUE | 1 | yes | YES | on | ON)
      enabled=true
      ;;
    false | FALSE | 0 | no | NO | off | OFF)
      enabled=false
      ;;
    *)
      die "LOAD_PLANNING_PLAN_INSIGHTS_ENABLED 只能是 true 或 false。"
      ;;
  esac
  case "${provider}" in
    rule_based | codex_cli | openai_responses)
      ;;
    *)
      die "LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER 只能是 rule_based、codex_cli 或 openai_responses。"
      ;;
  esac

  export LOAD_PLANNING_PLAN_INSIGHTS_ENABLED="${enabled}"
  export LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER="${provider}"
  LOAD_PLANNING_INSIGHT_RUNTIME_MODE="${enabled}:${provider}"
  export LOAD_PLANNING_INSIGHT_RUNTIME_MODE

  if [[ "${enabled}" == "true" && "${provider}" == "codex_cli" ]]; then
    require_command codex
    if ! codex_login_is_ready; then
      warn "Codex CLI 尚未登录。网站仍能启动，但“智能解读”会自动降级为规则说明。"
      warn "只需在本机执行一次 codex login；之后重新运行同一条 --with-codex 命令。"
    fi
  fi
  if [[ "${enabled}" == "true" \
    && "${provider}" == "openai_responses" \
    && -z "${LOCAL_LOAD_PLANNING_OPENAI_API_KEY}" ]]; then
    warn "OpenAI Responses API 模式已开启，但本机没有配置 OPENAI_API_KEY。"
    warn "网站仍能启动；“智能解读”会降级为规则说明。请只在未提交的 .env.local 中补 Key。"
  fi

  [[ -f "${mode_file}" ]] && previous_mode="$(tr -d '[:space:]' < "${mode_file}")"
  if managed_pid="$(read_managed_pid "load-planning-service")"; then
    if [[ "${force_managed_restart}" == "true" ]]; then
      info "已显式选择 OpenAI，将重启脚本管理的智能装载服务以重新读取 Key 和模型配置。"
      stop_managed_process "load-planning-service" 'uvicorn.*app\.main:app'
    elif [[ -z "${previous_mode}" \
      || "${previous_mode}" != "${LOAD_PLANNING_INSIGHT_RUNTIME_MODE}" ]]; then
      if [[ -z "${previous_mode}" ]]; then
        info "现有智能装载进程没有解读模式记录，将重启一次以应用当前配置。"
      else
        info "智能装载解读模式将从 ${previous_mode} 切换为 ${LOAD_PLANNING_INSIGHT_RUNTIME_MODE}。"
      fi
      stop_managed_process "load-planning-service" 'uvicorn.*app\.main:app'
    fi
  elif port_is_listening 8200; then
    if [[ "${force_managed_restart}" == "true" ]]; then
      warn "8200 由外部进程运行，脚本不会停止它，也无法让它重新读取 OpenAI Key/模型；如需更新，请先手工停止该进程。"
    else
      warn "8200 由外部进程运行，脚本无法切换其智能解读配置；如需切换，请先手工停止该进程。"
    fi
  fi
}

record_load_planning_insight_mode() {
  if read_managed_pid "load-planning-service" >/dev/null 2>&1; then
    printf '%s\n' "${LOAD_PLANNING_INSIGHT_RUNTIME_MODE}" \
      > "${PID_DIR}/load-planning-service.insight-mode"
  fi
}

print_load_planning_insight_status() {
  if [[ "${LOAD_PLANNING_PLAN_INSIGHTS_ENABLED}" != "true" ]]; then
    info "  方案智能解读：关闭（需要时加 --with-rules、--with-codex 或 --with-openai）"
    return 0
  fi

  case "${LOAD_PLANNING_PLAN_INSIGHTS_PROVIDER}" in
    codex_cli)
      info "  方案智能解读：本机 Codex CLI（失败时自动降级为规则说明）"
      ;;
    openai_responses)
      info "  方案智能解读：OpenAI Responses API（失败时自动降级为规则说明）"
      ;;
    rule_based)
      info "  方案智能解读：本地规则说明（不联网）"
      ;;
  esac
}

assert_port_available_or_healthy() {
  local port="$1"
  local health_url="$2"
  local label="$3"

  if ! port_is_listening "${port}"; then
    return 0
  fi

  if curl --fail --silent --show-error --max-time 2 "${health_url}" >/dev/null 2>&1; then
    info "${label} 已在端口 ${port} 运行，复用现有服务。"
    return 1
  fi

  die "端口 ${port} 已被其他进程占用，且不是可用的 ${label}。请先自行处理端口冲突。"
}

run_alembic_migration() {
  local service_directory="$1"
  local label="$2"
  local log_path="$3"
  local archive_scope="$4"

  rotate_current_log "${log_path}" "${archive_scope}" \
    || die "${label} 迁移日志轮转未完成；未执行数据库迁移。"
  info "执行 ${label} 数据库迁移。"
  (
    cd "${service_directory}"
    "${PYTHON_BIN}" -m alembic upgrade head
  ) >>"${log_path}" 2>&1 || die "${label} 数据库迁移失败。请查看日志：${log_path}"
}

assert_alembic_at_head() {
  local service_directory="$1"
  local label="$2"
  local log_path="$3"
  local archive_scope="$4"

  # 端口健康只说明旧进程还能响应，不能证明当前代码需要的新表已经迁移。
  # 复用外部进程时不擅自升级数据库，但必须确认本机目标库已经位于所有 Alembic head；
  # 否则基础健康检查可能通过，进入新接口后才暴露“表不存在”。
  rotate_current_log "${log_path}" "${archive_scope}" \
    || die "${label} 迁移检查日志轮转未完成；未检查数据库版本。"
  info "检查复用的 ${label} 数据库迁移版本。"
  if (
    cd "${service_directory}"
    "${PYTHON_BIN}" -m alembic current --check-heads
  ) >>"${log_path}" 2>&1; then
    return 0
  fi

  die "复用的 ${label} 数据库未迁移到当前 head，或无法确认迁移状态。请先正常停止旧服务，再重新运行一键启动。日志：${log_path}"
}

start_link_service() {
  local service_directory="${REPO_ROOT}/services/link-booking-service"
  local log_path="${LOG_DIR}/link-booking-service.log"
  local migration_log_path="${LOG_DIR}/link-booking-service-alembic.log"

  if ! assert_port_available_or_healthy \
    8000 "http://127.0.0.1:8000/health/live" "LINK Python 服务"; then
    assert_alembic_at_head \
      "${service_directory}" \
      "LINK Python" \
      "${migration_log_path}" \
      "link-booking-service-alembic"
    return 0
  fi

  run_alembic_migration \
    "${service_directory}" \
    "LINK Python" \
    "${migration_log_path}" \
    "link-booking-service-alembic"
  start_managed_process \
    "link-booking-service" \
    "${service_directory}" \
    'uvicorn.*app\.main:app' \
    "${log_path}" \
    "${PYTHON_BIN}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  wait_for_http \
    "http://127.0.0.1:8000/health/live" \
    "LINK Python 服务" \
    "${log_path}"
}

start_load_planning_service() {
  local service_directory="${REPO_ROOT}/services/load-planning-service"
  local log_path="${LOG_DIR}/load-planning-service.log"
  local migration_log_path="${LOG_DIR}/load-planning-service-alembic.log"

  if ! assert_port_available_or_healthy \
    8200 "http://127.0.0.1:8200/health/live" "智能装载 Python 服务"; then
    assert_alembic_at_head \
      "${service_directory}" \
      "智能装载 Python" \
      "${migration_log_path}" \
      "load-planning-service-alembic"
    record_load_planning_insight_mode
    return 0
  fi

  run_alembic_migration \
    "${service_directory}" \
    "智能装载 Python" \
    "${migration_log_path}" \
    "load-planning-service-alembic"

  # 只让这个 Python 子进程继承模型密钥。使用 export 而不是 `env KEY=... command`，
  # 可以避免 Key 出现在 ps 命令参数；进程启动后立即从父启动脚本环境中移除。
  if [[ -n "${LOCAL_LOAD_PLANNING_OPENAI_API_KEY}" ]]; then
    export OPENAI_API_KEY="${LOCAL_LOAD_PLANNING_OPENAI_API_KEY}"
  fi
  start_managed_process \
    "load-planning-service" \
    "${service_directory}" \
    'uvicorn.*app\.main:app' \
    "${log_path}" \
    "${PYTHON_BIN}" -m uvicorn app.main:app --host 127.0.0.1 --port 8200
  unset OPENAI_API_KEY
  wait_for_http \
    "http://127.0.0.1:8200/health/live" \
    "智能装载 Python 服务" \
    "${log_path}"
  record_load_planning_insight_mode
}

start_java_bff() {
  local service_directory="${REPO_ROOT}/platform-bff"
  local log_path="${LOG_DIR}/platform-bff.log"

  if ! assert_port_available_or_healthy \
    8080 "http://127.0.0.1:8080/actuator/health" "Java BFF"; then
    return 0
  fi

  start_managed_process \
    "platform-bff" \
    "${service_directory}" \
    'maven|spring-boot:run|platform-bff' \
    "${log_path}" \
    mvn spring-boot:run
  wait_for_http \
    "http://127.0.0.1:8080/actuator/health" \
    "Java BFF" \
    "${log_path}" \
    120
}

verify_load_planning_bff_chain() {
  local probe_script="${SCRIPT_DIR}/load-planning-e2e.py"
  local log_path="${LOG_DIR}/platform-bff.log"

  [[ -f "${probe_script}" ]] || die "缺少真实链路探活脚本：${probe_script}"
  info "验证 Java BFF → Nacos → 智能装载 Python → MySQL 真实调用链。"
  "${PYTHON_BIN}" "${probe_script}" --probe-only --wait-seconds 90 \
    || die "智能装载真实调用链未就绪。请查看日志：${log_path}"
}

start_platform_web() {
  local frontend_directory="${REPO_ROOT}/platform-web"
  local log_path="${LOG_DIR}/platform-web.log"

  if ! assert_port_available_or_healthy \
    5173 "http://127.0.0.1:5173/" "平台网站"; then
    return 0
  fi

  ensure_node_modules "${frontend_directory}" "平台网站"
  start_managed_process \
    "platform-web" \
    "${frontend_directory}" \
    'node.*vite' \
    "${log_path}" \
    "${frontend_directory}/node_modules/.bin/vite" \
    --host 127.0.0.1 --port 5173 --strictPort
  wait_for_http \
    "http://127.0.0.1:5173/" \
    "平台网站" \
    "${log_path}"
}

start_carrier_simulator() {
  local service_directory="${REPO_ROOT}/services/link-carrier-simulator"
  local log_path="${LOG_DIR}/link-carrier-simulator.log"
  local migration_log_path="${LOG_DIR}/link-carrier-simulator-alembic.log"

  if ! assert_port_available_or_healthy \
    8100 "http://127.0.0.1:8100/health/live" "模拟承运人服务"; then
    return 0
  fi

  run_alembic_migration \
    "${service_directory}" \
    "模拟承运人" \
    "${migration_log_path}" \
    "link-carrier-simulator-alembic"
  start_managed_process \
    "link-carrier-simulator" \
    "${service_directory}" \
    'uvicorn.*app\.main:app' \
    "${log_path}" \
    "${PYTHON_BIN}" -m uvicorn app.main:app --host 127.0.0.1 --port 8100
  wait_for_http \
    "http://127.0.0.1:8100/health/live" \
    "模拟承运人服务" \
    "${log_path}"
}

start_carrier_portal() {
  local frontend_directory="${REPO_ROOT}/carrier-simulator-web"
  local log_path="${LOG_DIR}/carrier-simulator-web.log"

  if ! assert_port_available_or_healthy \
    5174 "http://127.0.0.1:5174/" "模拟承运人门户"; then
    return 0
  fi

  ensure_node_modules "${frontend_directory}" "模拟承运人门户"
  start_managed_process \
    "carrier-simulator-web" \
    "${frontend_directory}" \
    'node.*vite' \
    "${log_path}" \
    "${frontend_directory}/node_modules/.bin/vite" \
    --host 127.0.0.1 --port 5174 --strictPort
  wait_for_http \
    "http://127.0.0.1:5174/" \
    "模拟承运人门户" \
    "${log_path}"
}

stop_managed_process() {
  local service_name="$1"
  local expected_pattern="$2"
  local pid
  local pid_file

  pid_file="$(pid_file_for "${service_name}")"
  if ! pid="$(read_managed_pid "${service_name}")"; then
    rm -f "${pid_file}"
    info "${service_name} 没有由本脚本管理的运行进程。"
    return 0
  fi

  if ! process_command_matches "${pid}" "${expected_pattern}"; then
    warn "${service_name} 的 PID ${pid} 当前命令不匹配；为避免误杀，不会停止它。"
    rm -f "${pid_file}"
    return 0
  fi

  kill "${pid}" >/dev/null 2>&1 || true
  local attempt=1
  while (( attempt <= 10 )); do
    kill -0 "${pid}" >/dev/null 2>&1 || break
    sleep 1
    ((attempt += 1))
  done

  if kill -0 "${pid}" >/dev/null 2>&1; then
    warn "${service_name} 没有在 10 秒内退出；为避免误杀，不发送强制 kill。"
    return 0
  fi

  rm -f "${pid_file}"
  case "${service_name}" in
    link-booking-service)
      rm -f "${PID_DIR}/link-booking-service.mode"
      ;;
    load-planning-service)
      rm -f "${PID_DIR}/load-planning-service.insight-mode"
      ;;
  esac
  info "已停止 ${service_name}。"
}

managed_process_pattern() {
  local service_name="$1"

  case "${service_name}" in
    link-booking-service | load-planning-service | link-carrier-simulator)
      printf '%s\n' 'uvicorn.*app\.main:app'
      ;;
    platform-bff)
      printf '%s\n' 'maven|spring-boot:run|platform-bff'
      ;;
    platform-web | carrier-simulator-web)
      printf '%s\n' 'node.*vite'
      ;;
    *)
      return 1
      ;;
  esac
}

stop_managed_process_if_pid() {
  local service_name="$1"
  local expected_pid="$2"
  local expected_pattern
  local pid_file
  local recorded_pid=""

  expected_pattern="$(managed_process_pattern "${service_name}")" \
    || die "会话清单包含未知服务：${service_name}"
  pid_file="$(pid_file_for "${service_name}")"

  if [[ -f "${pid_file}" ]]; then
    recorded_pid="$(tr -d '[:space:]' < "${pid_file}")"
  fi
  if [[ "${recorded_pid}" != "${expected_pid}" ]]; then
    info "${service_name} 已不再属于本次会话（期望 PID ${expected_pid}），不会停止。"
    return 0
  fi

  if ! kill -0 "${expected_pid}" >/dev/null 2>&1; then
    rm -f "${pid_file}"
    info "${service_name} 的本次会话进程已经退出。"
    return 0
  fi
  if ! process_command_matches "${expected_pid}" "${expected_pattern}"; then
    warn "${service_name} 的 PID ${expected_pid} 命令已变化；为避免误杀，不会停止它。"
    rm -f "${pid_file}"
    return 0
  fi

  kill "${expected_pid}" >/dev/null 2>&1 || true
  local attempt=1
  while (( attempt <= 10 )); do
    kill -0 "${expected_pid}" >/dev/null 2>&1 || break
    sleep 1
    ((attempt += 1))
  done

  if kill -0 "${expected_pid}" >/dev/null 2>&1; then
    warn "${service_name} 没有在 10 秒内退出；为避免误杀，不发送强制 kill。"
    return 0
  fi

  rm -f "${pid_file}"
  case "${service_name}" in
    link-booking-service)
      rm -f "${PID_DIR}/link-booking-service.mode"
      ;;
    load-planning-service)
      rm -f "${PID_DIR}/load-planning-service.insight-mode"
      ;;
  esac
  info "已停止本次会话启动的 ${service_name}。"
}

print_process_status() {
  local service_name="$1"
  local expected_pattern="$2"
  local port="$3"
  local pid

  if pid="$(read_managed_pid "${service_name}")"; then
    if process_command_matches "${pid}" "${expected_pattern}"; then
      printf '%-24s RUNNING  managed PID %s (port %s)\n' "${service_name}" "${pid}" "${port}"
      return 0
    fi
    printf '%-24s WARNING  stale PID record (port %s)\n' "${service_name}" "${port}"
    return 0
  fi

  if port_is_listening "${port}"; then
    printf '%-24s RUNNING  external process (port %s)\n' "${service_name}" "${port}"
  else
    printf '%-24s STOPPED  (port %s)\n' "${service_name}" "${port}"
  fi
}
