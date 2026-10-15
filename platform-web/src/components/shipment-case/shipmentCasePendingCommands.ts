/**
 * Shipment Case 页面待确认命令的会话级存储。
 *
 * 为什么不是只把 key 放在组件 ref 中：
 * - POST 可能已经在服务端成功，但响应在返回浏览器前中断；
 * - 此时刷新页面或关闭再打开抽屉，下一次重试仍必须携带原 Idempotency-Key；
 * - 否则服务端无法判断“这是一条旧命令的重试”还是“一条全新的命令”。
 *
 * 为什么使用 sessionStorage 而不是 localStorage：
 * - 待确认命令只需要跨当前标签页刷新恢复；
 * - 标签页会话结束后无需长期保留这类技术状态；
 * - 再配合 TTL 和数量上限，避免异常命令无限积累。
 */

export type ShipmentCasePendingCommandType =
  | 'CREATE_CASE'
  | 'BIND_RESOURCE'
  | 'CHANGE_STATUS'

export interface ShipmentCasePendingCommand {
  commandType: ShipmentCasePendingCommandType
  scope: string
  fingerprint: string
  key: string
  updatedAt: number
}

interface PendingCommandStore {
  version: 1
  records: Record<string, ShipmentCasePendingCommand>
}

interface GetOrCreatePendingCommandOptions {
  commandType: ShipmentCasePendingCommandType
  scope: string
  fingerprint: string
  keyPrefix: string
}

/**
 * 这个 key 是浏览器 sessionStorage 中的命名空间，不是发给服务端的 Idempotency-Key。
 */
export const SHIPMENT_CASE_PENDING_COMMAND_STORAGE_KEY =
  'customs-affairs-os.shipment-case.pending-commands.v1'

/**
 * sessionStorage 本身会随标签页会话结束而清理；六小时 TTL 用于处理长时间不关闭的工作台。
 */
const PENDING_COMMAND_TTL_MS = 6 * 60 * 60 * 1000
const MAX_PENDING_COMMANDS = 24

/**
 * 某些隐私模式或受限浏览器可能禁止 sessionStorage。
 * 这时退化为当前页面内存，至少不影响本次页面会话中的安全重试。
 */
let memoryFallback: PendingCommandStore = {
  version: 1,
  records: {},
}

function commandId(commandType: ShipmentCasePendingCommandType, scope: string): string {
  return `${commandType}::${scope}`
}

function isPendingCommand(value: unknown): value is ShipmentCasePendingCommand {
  if (!value || typeof value !== 'object') return false

  const candidate = value as Partial<ShipmentCasePendingCommand>
  return (
    ['CREATE_CASE', 'BIND_RESOURCE', 'CHANGE_STATUS'].includes(
      candidate.commandType ?? '',
    ) &&
    typeof candidate.scope === 'string' &&
    candidate.scope.length > 0 &&
    typeof candidate.fingerprint === 'string' &&
    candidate.fingerprint.length > 0 &&
    typeof candidate.key === 'string' &&
    candidate.key.length > 0 &&
    typeof candidate.updatedAt === 'number' &&
    Number.isFinite(candidate.updatedAt)
  )
}

function normalizeStore(store: PendingCommandStore, now = Date.now()): PendingCommandStore {
  const validRecords = Object.entries(store.records)
    .filter(([, record]) => {
      return isPendingCommand(record) && now - record.updatedAt <= PENDING_COMMAND_TTL_MS
    })
    .sort(([, left], [, right]) => right.updatedAt - left.updatedAt)
    .slice(0, MAX_PENDING_COMMANDS)

  return {
    version: 1,
    records: Object.fromEntries(validRecords),
  }
}

function writeStore(store: PendingCommandStore): void {
  const normalized = normalizeStore(store)
  memoryFallback = normalized

  if (typeof window === 'undefined') return

  try {
    window.sessionStorage.setItem(
      SHIPMENT_CASE_PENDING_COMMAND_STORAGE_KEY,
      JSON.stringify(normalized),
    )
  } catch {
    // sessionStorage 不可用时继续使用 memoryFallback，不让技术存储问题阻断业务表单。
  }
}

function readStore(): PendingCommandStore {
  if (typeof window === 'undefined') {
    memoryFallback = normalizeStore(memoryFallback)
    return memoryFallback
  }

  try {
    const raw = window.sessionStorage.getItem(SHIPMENT_CASE_PENDING_COMMAND_STORAGE_KEY)
    if (!raw) {
      memoryFallback = normalizeStore(memoryFallback)
      return memoryFallback
    }

    const parsed = JSON.parse(raw) as Partial<PendingCommandStore>
    const candidate: PendingCommandStore = {
      version: 1,
      records:
        parsed.version === 1 && parsed.records && typeof parsed.records === 'object'
          ? (parsed.records as Record<string, ShipmentCasePendingCommand>)
          : {},
    }
    const normalized = normalizeStore(candidate)

    // 每次读取都顺便落回清理后的版本，过期或超量记录不会一直占用浏览器存储。
    writeStore(normalized)
    return normalized
  } catch {
    memoryFallback = normalizeStore(memoryFallback)
    return memoryFallback
  }
}

/**
 * 读取某个命令范围内仍待确认的命令。
 *
 * scope 规则：
 * - 创建案件固定使用 create-form；
 * - 资源绑定和状态变化使用 caseNumber。
 */
export function getPendingShipmentCaseCommand(
  commandType: ShipmentCasePendingCommandType,
  scope: string,
): ShipmentCasePendingCommand | null {
  const store = readStore()
  return store.records[commandId(commandType, scope)] ?? null
}

/**
 * 为规范化请求取得 Idempotency-Key。
 *
 * 同一 commandType + scope + fingerprint：
 * - 返回原 key，表示重试同一条未确认命令。
 *
 * 同一 commandType + scope，但 fingerprint 已变化：
 * - 用新记录替换旧记录，表示用户已经提交一条实质不同的新命令。
 */
export function getOrCreateShipmentCaseCommandKey(
  options: GetOrCreatePendingCommandOptions,
): string {
  const store = readStore()
  const id = commandId(options.commandType, options.scope)
  const existing = store.records[id]
  const now = Date.now()

  if (existing?.fingerprint === options.fingerprint) {
    existing.updatedAt = now
    writeStore(store)
    return existing.key
  }

  const key = `${options.keyPrefix}-${crypto.randomUUID()}`
  store.records[id] = {
    commandType: options.commandType,
    scope: options.scope,
    fingerprint: options.fingerprint,
    key,
    updatedAt: now,
  }
  writeStore(store)
  return key
}

/**
 * 删除已经得到确定结果或被用户放弃的命令。
 *
 * fingerprint 可选：传入时只删除完全匹配的那一条，避免未来并发 UI 误删更新后的命令。
 */
export function clearPendingShipmentCaseCommand(
  commandType: ShipmentCasePendingCommandType,
  scope: string,
  fingerprint?: string,
): void {
  const store = readStore()
  const id = commandId(commandType, scope)
  const existing = store.records[id]
  if (!existing || (fingerprint && existing.fingerprint !== fingerprint)) return

  delete store.records[id]
  writeStore(store)
}
