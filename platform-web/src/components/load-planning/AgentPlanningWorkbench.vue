<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import {
  createLoadPlanningAgentRun,
  decideLoadPlanningAgentRun,
  generateLoadPlanningPlanInsight,
  getLoadPlanningAgentRun,
  getLoadPlanningAgentRunHistory,
  getLoadPlanVersions,
  lockLoadPlanVersion,
  replanLoadPlanningAgentRun,
  resumeLoadPlanningAgentRun,
  type LoadPlanningAgentAnswerValue,
  type LoadPlanningAgentCurrentStep,
  type LoadPlanningAgentPlan,
  type LoadPlanningAgentQuestion,
  type LoadPlanningAgentRun,
  type LoadPlanningPlanInsight,
  type LoadPlanVersion,
  type LoadPlanningPlanPallet,
  type LoadPlanningTask,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import PalletPlan3D from '@/components/load-planning/PalletPlan3D.vue'

const props = defineProps<{
  modelValue: boolean
  task: LoadPlanningTask
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const run = ref<LoadPlanningAgentRun | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const answerDraft = reactive<Record<string, string>>({})
const selectedPlanCode = ref('')
const selectedPalletIndex = ref(1)
const decisionComment = ref('')
const replanReason = ref('')
const runOrigin = ref<'RESTORED' | 'NEW' | null>(null)
const planVersions = ref<LoadPlanVersion[]>([])
const planVersionsLoading = ref(false)
const planVersionError = ref('')
const locking = ref(false)
const lockReason = ref('仓库负责人确认，发布为正式作业版本')
const planInsight = ref<LoadPlanningPlanInsight | null>(null)
const planInsightLoading = ref(false)
const planInsightError = ref('')
let planInsightRequestSequence = 0

/**
 * 浏览器只保存 runId，不保存运行内容。
 *
 * 真正的状态、问题、方案和审计事件仍以 Python + MySQL 为准；localStorage
 * 只是一个“找到上次运行入口”的索引。这样刷新页面不会把整份业务状态复制到
 * 浏览器，也不会让前端自行推进智能体步骤。
 */
const runStorageKey = computed(
  () => `customs-affairs-os:load-planning:agent-run:${props.task.taskNumber}`,
)
const createIdempotencyKeyStorageKey = computed(
  () => `customs-affairs-os:load-planning:create-key:${props.task.taskNumber}`,
)

const statusLabel: Record<string, string> = {
  AWAITING_INPUT: '等待人工补充',
  READY_FOR_REVIEW: '等待方案审批',
  APPROVED: '候选方案已批准',
  REJECTED: '方案已驳回',
  FAILED: '运行失败',
}

/** 与 Python 持久化状态机一一对应，新增步骤会在 TypeScript 编译期暴露遗漏。 */
const stepLabel: Record<LoadPlanningAgentCurrentStep, string> = {
  CHECKING_CONSTRAINTS: '检查任务约束',
  AWAITING_CONSTRAINTS: '等待人工补充约束',
  GENERATING_PLANS: '生成装载方案',
  AWAITING_REVIEW: '等待人工审批',
  FAILED: '执行失败',
  COMPLETED: '流程完成',
}

const selectedPlan = computed<LoadPlanningAgentPlan | null>(() => {
  if (!run.value) return null
  return (
    run.value.plans.find((plan) => plan.planCode === selectedPlanCode.value) ??
    run.value.plans[0] ??
    null
  )
})

const selectedPallet = computed<LoadPlanningPlanPallet | null>(() => {
  const plan = selectedPlan.value
  if (!plan) return null
  return (
    plan.pallets.find((pallet) => pallet.palletIndex === selectedPalletIndex.value) ??
    plan.pallets[0] ??
    null
  )
})

const selectedPalletSpec = computed(() => {
  const palletCode = selectedPallet.value?.palletCode ?? run.value?.selectedPalletCode
  return props.task.palletSpecs.find((spec) => spec.palletCode === palletCode) ?? null
})

/**
 * 只展示与“当前运行 + 当前选中方案”完全一致的解读。
 *
 * 用户可能在解读请求返回前切换候选方案。这里再次核对 runId 和 planCode，避免把
 * 旧请求的文字挂到新方案上；审批按钮也从不读取该对象。
 */
const visiblePlanInsight = computed<LoadPlanningPlanInsight | null>(() => {
  if (
    planInsight.value?.runId !== run.value?.runId ||
    planInsight.value?.planCode !== selectedPlan.value?.planCode
  ) {
    return null
  }
  return planInsight.value
})

const canApprove = computed(
  () =>
    run.value?.status === 'READY_FOR_REVIEW' &&
    selectedPlan.value !== null &&
    selectedPlan.value.feasible,
)

/** 当前正式版本永远取最高版本号；后端同样通过任务行锁维护单线 V1 → V2 链。 */
const currentPlanVersion = computed<LoadPlanVersion | null>(
  () =>
    [...planVersions.value].sort((left, right) => right.versionNumber - left.versionNumber)[0] ??
    null,
)

/** 同一 sourceRunId 最多只能发布一个正式版本，网络重试会返回这一条旧记录。 */
const runPlanVersion = computed<LoadPlanVersion | null>(() => {
  if (!run.value) return null
  return (
    planVersions.value.find((version) => version.sourceRunId === run.value?.runId) ?? null
  )
})

const canLock = computed(
  () =>
    run.value?.status === 'APPROVED' &&
    runPlanVersion.value === null &&
    lockReason.value.trim().length > 0,
)

function close(): void {
  if (!loading.value && !locking.value) emit('update:modelValue', false)
}

function resetAnswers(questions: LoadPlanningAgentQuestion[]): void {
  for (const key of Object.keys(answerDraft)) delete answerDraft[key]
  for (const question of questions) answerDraft[question.key] = ''
}

function acceptRun(nextRun: LoadPlanningAgentRun): void {
  const previousRunId = run.value?.runId
  run.value = nextRun
  resetAnswers(nextRun.questions)

  // 新运行拥有独立的方案集合，不能沿用上一条运行生成的自然语言解读。
  if (previousRunId && previousRunId !== nextRun.runId) {
    planInsight.value = null
    planInsightError.value = ''
  }

  // APPROVED/REJECTED/FAILED 仍可能需要正式发布或重新规划，因此也保留 runId。
  // 用户只有明确点击“发起新试算”时才轮换入口，页面刷新不会擅自跳过待处理终态。
  persistRunId(nextRun)

  const currentSelection = nextRun.plans.find(
    (plan) => plan.planCode === selectedPlanCode.value,
  )
  const defaultPlan =
    nextRun.plans.find((plan) => plan.planCode === nextRun.selectedPlanCode) ??
    currentSelection ??
    nextRun.plans.find((plan) => plan.feasible) ??
    nextRun.plans[0]
  selectedPlanCode.value = defaultPlan?.planCode ?? ''
  selectedPalletIndex.value = defaultPlan?.pallets[0]?.palletIndex ?? 1
}

function clearPersistedRunId(): void {
  try {
    window.localStorage.removeItem(runStorageKey.value)
  } catch {
    // 隐私模式或浏览器策略可能禁用 localStorage；这不应阻断服务端运行。
  }
}

/**
 * 创建请求的幂等键也只保存“请求入口”，不保存业务状态。
 *
 * 如果 POST 已经到达 Python，但浏览器在响应返回前断网，下一次点击重试会复用这个
 * key。Python 会按 task + key 找回原运行；请求成功后再清掉 key，避免下一次用户
 * 明确开始新规划时误复用旧请求。
 */
function clearPendingCreateIdempotencyKey(): void {
  try {
    window.localStorage.removeItem(createIdempotencyKeyStorageKey.value)
  } catch {
    // localStorage 不可用时仍可正常发送请求，只是无法跨刷新复用幂等键。
  }
}

function readPendingCreateIdempotencyKey(): string | null {
  try {
    const value = window.localStorage
      .getItem(createIdempotencyKeyStorageKey.value)
      ?.trim()
    return value || null
  } catch {
    return null
  }
}

function createIdempotencyKey(): string {
  const browserCrypto = globalThis.crypto
  if (browserCrypto?.randomUUID) return browserCrypto.randomUUID()
  return `load-planning-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function getOrCreatePendingCreateIdempotencyKey(): string {
  const existing = readPendingCreateIdempotencyKey()
  if (existing) return existing

  const created = createIdempotencyKey()
  try {
    window.localStorage.setItem(createIdempotencyKeyStorageKey.value, created)
  } catch {
    // 没有 localStorage 时仍把 key 用于本次请求；刷新后由用户重新发起。
  }
  return created
}

/**
 * 重规划的幂等键按“父运行 + 原因”保存。
 *
 * 网络在服务端已创建新运行后断开时，业务人员再次点击相同请求会复用 key 并取回同一条
 * 子运行；如果修改了重规划原因，则生成新 key，避免同一个 key 对应两份不同请求体。
 */
function getOrCreatePendingReplanIdempotencyKey(runId: string, reason: string): string {
  const storageKey = `customs-affairs-os:load-planning:replan-key:${runId}`
  try {
    const stored = window.localStorage.getItem(storageKey)
    if (stored) {
      const parsed = JSON.parse(stored) as { reason?: unknown; key?: unknown }
      if (
        parsed.reason === reason &&
        typeof parsed.key === 'string' &&
        parsed.key.trim().length > 0
      ) {
        return parsed.key
      }
    }

    const key = createIdempotencyKey()
    window.localStorage.setItem(storageKey, JSON.stringify({ reason, key }))
    return key
  } catch {
    return createIdempotencyKey()
  }
}

function clearPendingReplanIdempotencyKey(runId: string): void {
  try {
    window.localStorage.removeItem(
      `customs-affairs-os:load-planning:replan-key:${runId}`,
    )
  } catch {
    // 浏览器存储不可用不影响服务端按本次请求 key 处理。
  }
}

function readPersistedRunId(): string | null {
  try {
    const value = window.localStorage.getItem(runStorageKey.value)?.trim()
    return value || null
  } catch {
    // localStorage 不可用时退化为“本次打开新建”，不把浏览器能力当成业务状态。
    return null
  }
}

function persistRunId(nextRun: LoadPlanningAgentRun): void {
  try {
    window.localStorage.setItem(runStorageKey.value, nextRun.runId)
  } catch {
    // 保存入口失败不影响当前页面继续使用已从后端返回的运行状态。
  }
}

/**
 * 正式版本查询失败不应触发新建运行。
 *
 * AgentRun 和 LoadPlanVersion 是两条独立读取链：试算仍可查看，但页面必须明确提示版本
 * 历史暂时不可用，不能在“不知道当前 V 几”的情况下继续锁定。
 */
async function refreshPlanVersions(): Promise<void> {
  planVersionsLoading.value = true
  planVersionError.value = ''
  try {
    const versions = await getLoadPlanVersions(props.task.taskNumber)
    planVersions.value = [...versions].sort(
      (left, right) => right.versionNumber - left.versionNumber,
    )
  } catch (error) {
    planVersionError.value =
      error instanceof Error ? error.message : '读取正式装载方案版本失败。'
  } finally {
    planVersionsLoading.value = false
  }
}

/**
 * 现有 API 对 HTTP 错误只抛出 Error 文本，暂时没有暴露 status 字段。
 *
 * 只有明确的“运行不存在/已被清理”才自动新建；网络错误、502、503 等情况
 * 必须停在页面上等待用户重试，否则可能因为暂时断网而重复创建业务运行。
 */
function isMissingPersistedRunError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.message.includes('运行不存在') || error.message.includes('已被清理'))
  )
}

async function createNewRun(): Promise<void> {
  planInsightRequestSequence += 1
  planInsight.value = null
  planInsightLoading.value = false
  planInsightError.value = ''
  loading.value = true
  errorMessage.value = ''
  run.value = null
  selectedPlanCode.value = ''
  decisionComment.value = ''
  runOrigin.value = 'NEW'
  const idempotencyKey = getOrCreatePendingCreateIdempotencyKey()
  try {
    const created = await createLoadPlanningAgentRun(props.task.taskNumber, {
      // 只有一种托盘时不需要让用户重复选择；多规格交给智能体生成结构化问题。
      selectedPalletCode:
        props.task.palletSpecs.length === 1 ? props.task.palletSpecs[0]!.palletCode : null,
      strategyCodes: ['STABLE', 'BALANCED', 'UTILIZATION'],
    }, idempotencyKey)
    clearPendingCreateIdempotencyKey()
    acceptRun(created)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '启动装载规划失败'
  } finally {
    loading.value = false
  }
}

/**
 * 用户明确点击“重新启动”时才强制创建新运行。
 *
 * 普通打开工作台走 restoreOrStartRun()，会先尝试恢复 localStorage 中的 runId；
 * 将两条路径分开可以避免刷新页面时无意中重复 POST 创建运行。
 */
async function startRun(): Promise<void> {
  clearPersistedRunId()
  // “重新启动”是用户明确要求新建一条运行，必须轮换掉可能因网络超时
  // 留下的旧创建请求 key。
  clearPendingCreateIdempotencyKey()
  await createNewRun()
}

/**
 * 浏览器没有入口时，从服务端完整历史恢复最近一条运行。
 *
 * 不能只查询 active 运行：APPROVED 仍可能等待正式发布，REJECTED/FAILED 仍需要在
 * 原审计链上重新规划。只有任务从未产生过运行时，才自动创建第一次试算。
 */
async function restoreLatestServerRunOrCreate(): Promise<void> {
  const history = await getLoadPlanningAgentRunHistory(props.task.taskNumber)
  const latest = history[0]
  if (latest) {
    runOrigin.value = 'RESTORED'
    acceptRun(latest)
    loading.value = false
    return
  }
  await createNewRun()
}

async function restoreOrStartRun(): Promise<void> {
  // 重新进入工作台时终止旧的展示请求；运行恢复和辅助解读是两条独立链路。
  planInsightRequestSequence += 1
  planInsight.value = null
  planInsightLoading.value = false
  planInsightError.value = ''
  loading.value = true
  errorMessage.value = ''
  run.value = null
  selectedPlanCode.value = ''
  decisionComment.value = ''
  runOrigin.value = null
  planVersions.value = []
  void refreshPlanVersions()

  const persistedRunId = readPersistedRunId()
  if (!persistedRunId) {
    try {
      // localStorage 只是快速入口；真正的跨设备恢复通过服务端完整历史完成。
      await restoreLatestServerRunOrCreate()
    } catch (error) {
      // 查询失败时不能盲目 POST 创建，否则一次网络抖动可能制造重复运行。
      errorMessage.value =
        error instanceof Error ? error.message : '查询装载规划历史失败，请稍后重试。'
      loading.value = false
    }
    return
  }

  try {
    const restored = await getLoadPlanningAgentRun(persistedRunId)
    runOrigin.value = 'RESTORED'
    acceptRun(restored)
    loading.value = false
  } catch (error) {
    if (isMissingPersistedRunError(error)) {
      clearPersistedRunId()
      try {
        await restoreLatestServerRunOrCreate()
      } catch (listError) {
        errorMessage.value =
          listError instanceof Error
            ? listError.message
            : '查询装载规划历史失败，请稍后重试。'
        loading.value = false
      }
      return
    }

    // 非 404 错误不自动 POST，避免网络波动时重复上传/重复建运行。
    errorMessage.value =
      error instanceof Error ? error.message : '恢复上次装载规划运行失败，请稍后重试。'
    loading.value = false
  }
}

function parseAnswer(question: LoadPlanningAgentQuestion): LoadPlanningAgentAnswerValue {
  const raw = answerDraft[question.key] ?? ''
  if (question.valueType === 'BOOLEAN') return raw === 'true'
  if (question.valueType === 'NUMBER' || question.valueType === 'INTEGER') {
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) throw new Error(`${question.label} 必须填写有效数字。`)
    return question.valueType === 'INTEGER' ? Math.trunc(parsed) : parsed
  }
  return raw.trim()
}

async function resumeRun(): Promise<void> {
  if (!run.value) return

  try {
    const answers = run.value.questions.map((question) => {
      const raw = answerDraft[question.key] ?? ''
      if (question.required && raw === '') throw new Error(`请填写“${question.label}”。`)
      return { key: question.key, value: parseAnswer(question) }
    })

    loading.value = true
    errorMessage.value = ''
    const resumed = await resumeLoadPlanningAgentRun(run.value.runId, {
      answers,
      expectedRunVersion: run.value.runVersion,
    })
    acceptRun(resumed)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '恢复装载规划失败'
  } finally {
    loading.value = false
  }
}

function selectPlan(plan: LoadPlanningAgentPlan): void {
  if (selectedPlanCode.value !== plan.planCode) {
    // 浏览器无法真正撤回已经发出的 HTTP 请求，但可以立即使旧响应失效并停止旧加载提示。
    planInsightRequestSequence += 1
    planInsightLoading.value = false
  }
  selectedPlanCode.value = plan.planCode
  selectedPalletIndex.value = plan.pallets[0]?.palletIndex ?? 1
  planInsightError.value = ''
}

const insightProviderLabel: Record<LoadPlanningPlanInsight['provider'], string> = {
  OPENAI: '模型服务',
  CODEX_CLI: '本地智能试验',
  RULE_BASED: '规则说明',
}

/**
 * 为当前候选方案请求一次辅助解读。
 *
 * 这个函数刻意不修改 run、selectedPlanCode、canApprove 或正式版本状态。即使请求失败，
 * 用户仍能依据结构化指标、规则告警和 3D 结果完成原有人工评审。
 */
async function generatePlanInsight(): Promise<void> {
  if (!run.value || !selectedPlan.value) return

  const requestedRunId = run.value.runId
  const requestedPlanCode = selectedPlan.value.planCode
  const requestSequence = ++planInsightRequestSequence
  planInsightLoading.value = true
  planInsightError.value = ''

  try {
    const generated = await generateLoadPlanningPlanInsight(
      requestedRunId,
      requestedPlanCode,
    )

    // 丢弃已过时的响应：例如用户请求期间切换了方案，或重新规划产生了新 run。
    if (
      requestSequence === planInsightRequestSequence &&
      run.value?.runId === requestedRunId &&
      selectedPlan.value?.planCode === requestedPlanCode
    ) {
      planInsight.value = generated
    }
  } catch (error) {
    if (requestSequence === planInsightRequestSequence) {
      planInsightError.value =
        error instanceof Error ? error.message : '方案解读暂时不可用，请稍后重试。'
    }
  } finally {
    if (requestSequence === planInsightRequestSequence) {
      planInsightLoading.value = false
    }
  }
}

async function submitDecision(decision: 'APPROVE' | 'REJECT'): Promise<void> {
  if (!run.value) return
  if (decision === 'APPROVE' && !canApprove.value) {
    errorMessage.value = '只能批准当前选中的可行方案。'
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    const decided = await decideLoadPlanningAgentRun(run.value.runId, {
      decision,
      planCode: decision === 'APPROVE' ? selectedPlan.value?.planCode ?? null : null,
      comment: decisionComment.value.trim() || null,
      expectedRunVersion: run.value.runVersion,
    })
    acceptRun(decided)
    if (decided.status === 'APPROVED') {
      // 正常情况下此时仍为空；重新读取可以覆盖另一个页面刚完成发布的并发场景。
      await refreshPlanVersions()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交人工决策失败'
  } finally {
    loading.value = false
  }
}

async function lockApprovedRun(): Promise<void> {
  if (!run.value || !canLock.value) return
  if (planVersionError.value) {
    errorMessage.value = '正式版本历史读取失败，不能安全判断当前版本号，请先刷新版本历史。'
    return
  }

  locking.value = true
  errorMessage.value = ''
  try {
    const locked = await lockLoadPlanVersion(run.value.runId, {
      expectedRunVersion: run.value.runVersion,
      expectedCurrentVersionNumber: currentPlanVersion.value?.versionNumber ?? 0,
      lockReason: lockReason.value.trim(),
    })
    const remaining = planVersions.value.filter(
      (version) => version.versionId !== locked.versionId,
    )
    planVersions.value = [locked, ...remaining].sort(
      (left, right) => right.versionNumber - left.versionNumber,
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '锁定正式装载方案失败'
  } finally {
    locking.value = false
  }
}

async function replanRun(): Promise<void> {
  if (!run.value || !['REJECTED', 'FAILED'].includes(run.value.status)) return
  const parentRunId = run.value.runId
  const reason =
    replanReason.value.trim() || '业务人员要求按当前已确认约束重新规划'
  const idempotencyKey = getOrCreatePendingReplanIdempotencyKey(parentRunId, reason)
  loading.value = true
  errorMessage.value = ''
  try {
    const replanned = await replanLoadPlanningAgentRun(
      parentRunId,
      { reason },
      idempotencyKey,
    )
    clearPendingReplanIdempotencyKey(parentRunId)
    replanReason.value = ''
    runOrigin.value = 'NEW'
    acceptRun(replanned)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '创建重新规划运行失败'
  } finally {
    loading.value = false
  }
}

function formatPercent(value: number): string {
  const percent = value <= 1 ? value * 100 : value
  return `${percent.toFixed(1)}%`
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function shortHash(value: string): string {
  return value.length > 16 ? `${value.slice(0, 12)}…${value.slice(-4)}` : value
}

watch(
  [() => props.modelValue, () => props.task.taskNumber],
  ([open]) => {
    if (open) void restoreOrStartRun()
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="modelValue" class="agent-workbench-backdrop">
    <section class="agent-workbench" aria-labelledby="agent-workbench-title">
      <header class="agent-workbench-header">
        <div>
          <span class="eyebrow">装载规划作业</span>
          <h2 id="agent-workbench-title">智能装载工作台</h2>
          <p>
            {{ task.taskNumber }} · {{ task.taskName }}。基于已确认约束生成、比较并审批装载方案。
          </p>
          <p v-if="runOrigin === 'RESTORED'" class="agent-recovery-note">
            已恢复本任务上次未完成的规划批次，可从当前业务步骤继续处理。
          </p>
          <p v-else-if="runOrigin === 'NEW'" class="agent-recovery-note">
            已创建新的规划批次；刷新页面后仍会继续当前批次，不会重复发起。
          </p>
        </div>
        <div class="agent-header-actions">
          <span v-if="run" class="agent-status-chip" :data-status="run.status">
            {{ statusLabel[run.status] ?? run.status }}
          </span>
          <button
            class="close-button"
            type="button"
            :disabled="loading || locking"
            @click="close"
          >
            <UiIcon name="close" />
          </button>
        </div>
      </header>

      <div class="agent-workbench-body">
        <aside class="agent-timeline-panel">
          <div class="agent-panel-title">
            <div>
              <strong>执行轨迹</strong>
              <span>全过程留痕</span>
            </div>
            <UiIcon name="clock" />
          </div>

          <ol v-if="run?.events.length" class="agent-timeline">
            <li v-for="event in run.events" :key="event.sequence">
              <i></i>
              <div>
                <span>{{ String(event.sequence).padStart(2, '0') }} · {{ event.eventType }}</span>
                <strong>{{ event.title }}</strong>
                <p>{{ event.detail }}</p>
                <small>{{ formatDate(event.createdAt) }}</small>
              </div>
            </li>
          </ol>
          <div v-else class="agent-timeline-empty">
            <span class="loading-ring"></span>
            <p>正在建立本次规划批次。</p>
          </div>

          <div v-if="run" class="agent-current-step">
            <span>当前步骤</span>
            <strong>{{ stepLabel[run.currentStep] ?? run.currentStep }}</strong>
            <small>批次 · {{ run.runId }}</small>
          </div>
        </aside>

        <main class="agent-main">
          <div class="agent-boundary-banner">
            <UiIcon name="shield" />
            <div>
              <strong>规划结果需人工确认</strong>
              <p>
                系统会检查坐标、碰撞、高度和重量约束；缺失的安全参数必须由业务人员确认后
                才能继续。
              </p>
            </div>
          </div>

          <div v-if="loading && !run" class="agent-loading">
            <span class="loading-ring"></span>
            <strong>正在读取任务约束并准备规划</strong>
            <p>资料完整时直接生成方案；存在缺项时会暂停并等待补充。</p>
          </div>

          <div v-if="errorMessage" class="feedback-banner error" role="alert">
            <span>!</span>
            <p>{{ errorMessage }}</p>
            <button v-if="!run" type="button" @click="startRun">重新启动</button>
          </div>

          <div v-if="planVersionError" class="feedback-banner error" role="alert">
            <span>!</span>
            <p>{{ planVersionError }} 正式发布已暂停，但仍可查看当前试算。</p>
            <button type="button" :disabled="planVersionsLoading" @click="refreshPlanVersions">
              {{ planVersionsLoading ? '正在刷新…' : '刷新版本历史' }}
            </button>
          </div>

          <section v-if="run?.status === 'AWAITING_INPUT'" class="agent-question-panel">
            <div class="agent-section-heading">
              <div>
                <span>约束确认</span>
                <h3>需要你确认 {{ run.questions.length }} 项约束</h3>
                <p>这些值会保存到本次试算，不会反向覆盖任务主数据。</p>
              </div>
              <span class="agent-step-number">01</span>
            </div>

            <form class="agent-question-grid" @submit.prevent="resumeRun">
              <label
                v-for="question in run.questions"
                :key="question.key"
                class="agent-question-card"
              >
                <span>
                  {{ question.label }}
                  <em v-if="question.unit">{{ question.unit }}</em>
                </span>
                <select
                  v-if="question.key === 'selected_pallet_code'"
                  v-model="answerDraft[question.key]"
                  :required="question.required"
                >
                  <option value="">请选择托盘规格</option>
                  <option
                    v-for="pallet in task.palletSpecs"
                    :key="pallet.palletCode"
                    :value="pallet.palletCode"
                  >
                    {{ pallet.palletName }} · {{ pallet.palletCode }}
                  </option>
                </select>
                <select
                  v-else-if="question.valueType === 'BOOLEAN'"
                  v-model="answerDraft[question.key]"
                  :required="question.required"
                >
                  <option value="">请选择</option>
                  <option value="true">是</option>
                  <option value="false">否</option>
                </select>
                <input
                  v-else-if="
                    question.valueType === 'NUMBER' || question.valueType === 'INTEGER'
                  "
                  v-model="answerDraft[question.key]"
                  type="number"
                  :step="question.valueType === 'INTEGER' ? 1 : 0.001"
                  :min="question.minValue ?? undefined"
                  :max="question.maxValue ?? undefined"
                  :required="question.required"
                />
                <input
                  v-else
                  v-model="answerDraft[question.key]"
                  type="text"
                  :required="question.required"
                />
                <small>{{ question.reason }}</small>
              </label>

              <div class="agent-question-actions">
                <button class="secondary-button" type="button" :disabled="loading" @click="close">
                  暂停，稍后处理
                </button>
                <button class="primary-button" type="submit" :disabled="loading">
                  {{ loading ? '正在恢复并计算…' : '提交约束并继续规划' }}
                </button>
              </div>
            </form>
          </section>

          <template v-if="run && run.plans.length">
            <section class="agent-plan-section">
              <div class="agent-section-heading">
                <div>
                  <span>方案评审</span>
                  <h3>对比候选装载方案</h3>
                  <p>可查看全部计算结果，但只能批准满足当前约束的可行方案。</p>
                </div>
                <span class="agent-step-number">02</span>
              </div>

              <div class="agent-plan-grid">
                <button
                  v-for="plan in run.plans"
                  :key="plan.planCode"
                  class="agent-plan-card"
                  :class="{
                    selected: selectedPlan?.planCode === plan.planCode,
                    infeasible: !plan.feasible,
                  }"
                  type="button"
                  @click="selectPlan(plan)"
                >
                  <span class="plan-strategy">{{ plan.strategyName }}</span>
                  <strong>{{ plan.score.toFixed(1) }}</strong>
                  <small>综合分</small>
                  <p>{{ plan.summary }}</p>
                  <dl>
                    <div><dt>托盘</dt><dd>{{ plan.metrics.totalPallets }}</dd></div>
                    <div><dt>未放置</dt><dd>{{ plan.metrics.unplacedBoxes }}</dd></div>
                    <div><dt>体积利用</dt><dd>{{ formatPercent(plan.metrics.volumeUtilization) }}</dd></div>
                    <div><dt>最高</dt><dd>{{ plan.metrics.maxLoadedHeightMm.toFixed(0) }} mm</dd></div>
                  </dl>
                  <span class="plan-feasibility" :data-feasible="plan.feasible">
                    {{ plan.feasible ? '可行方案' : '约束未满足' }}
                  </span>
                </button>
              </div>
            </section>

            <section v-if="selectedPlan" class="agent-review-grid">
              <div class="agent-viewer-column">
                <div class="agent-pallet-toolbar">
                  <div>
                    <strong>{{ selectedPlan.strategyName }}</strong>
                    <span>{{ selectedPlan.planCode }}</span>
                  </div>
                  <div class="agent-pallet-toolbar-actions">
                    <label>
                      查看托盘
                      <select v-model.number="selectedPalletIndex">
                        <option
                          v-for="pallet in selectedPlan.pallets"
                          :key="pallet.palletIndex"
                          :value="pallet.palletIndex"
                        >
                          #{{ pallet.palletIndex }} · {{ pallet.boxCount }} 箱 ·
                          {{ pallet.loadedHeightMm.toFixed(0) }} mm
                        </option>
                      </select>
                    </label>
                    <button
                      class="agent-insight-button"
                      type="button"
                      :disabled="planInsightLoading"
                      @click="generatePlanInsight"
                    >
                      <UiIcon name="file" />
                      {{ planInsightLoading ? '正在解读…' : '智能解读' }}
                    </button>
                  </div>
                </div>

                <PalletPlan3D
                  :pallet="selectedPallet"
                  :pallet-spec="selectedPalletSpec"
                />
              </div>

              <aside class="agent-review-panel">
                <div class="agent-panel-title">
                  <div>
                    <strong>方案评价</strong>
                    <span>规则综合评分</span>
                  </div>
                  <UiIcon name="check" />
                </div>

                <dl class="agent-metric-list">
                  <div>
                    <dt>总箱数</dt>
                    <dd>{{ selectedPlan.metrics.totalBoxes }}</dd>
                  </div>
                  <div>
                    <dt>未放置箱数</dt>
                    <dd>{{ selectedPlan.metrics.unplacedBoxes }}</dd>
                  </div>
                  <div>
                    <dt>平均成托高度</dt>
                    <dd>{{ selectedPlan.metrics.averageLoadedHeightMm.toFixed(0) }} mm</dd>
                  </div>
                  <div>
                    <dt>重量利用率</dt>
                    <dd>{{ formatPercent(selectedPlan.metrics.weightUtilization) }}</dd>
                  </div>
                  <div>
                    <dt>含托总重</dt>
                    <dd>{{ selectedPlan.metrics.totalGrossWeightKg.toFixed(1) }} kg</dd>
                  </div>
                </dl>

                <div class="agent-warning-list">
                  <strong>检查与提醒</strong>
                  <p v-if="selectedPlan.warnings.length === 0" class="agent-no-warning">
                    当前规则检查没有发现阻断项。
                  </p>
                  <p v-for="warning in selectedPlan.warnings" :key="warning">
                    <UiIcon name="alert" />
                    {{ warning }}
                  </p>
                </div>

                <template v-if="run.status === 'READY_FOR_REVIEW'">
                  <label class="form-field">
                    <span>审批备注</span>
                    <textarea
                      v-model="decisionComment"
                      maxlength="1000"
                      placeholder="记录选择原因、现场要求或驳回原因"
                    ></textarea>
                  </label>
                  <div class="agent-decision-actions">
                    <button
                      class="secondary-button danger"
                      type="button"
                      :disabled="loading"
                      @click="submitDecision('REJECT')"
                    >
                      驳回本次运行
                    </button>
                    <button
                      class="primary-button"
                      type="button"
                      :disabled="loading || !canApprove"
                      @click="submitDecision('APPROVE')"
                    >
                      批准当前可行方案
                    </button>
                  </div>
                </template>

                <div
                  v-else
                  class="agent-final-decision"
                  :data-status="run.status"
                >
                  <strong>{{ statusLabel[run.status] }}</strong>
                  <p>
                    {{
                      run.status === 'APPROVED'
                        ? runPlanVersion
                          ? `候选方案 ${run.selectedPlanCode ?? ''} 已发布为正式 V${runPlanVersion.versionNumber}。`
                          : `已批准候选方案 ${run.selectedPlanCode ?? ''}；它还不是现场执行版本，需要完成正式锁定。`
                      : '本次运行已停止，可关闭后重新发起规划。'
                    }}
                  </p>
                </div>

                <div
                  v-if="run.status === 'APPROVED'"
                  class="agent-publish-card"
                  :data-locked="Boolean(runPlanVersion)"
                >
                  <template v-if="runPlanVersion">
                    <div class="agent-publish-heading">
                      <div>
                        <span>正式执行方案</span>
                        <strong>正式 V{{ runPlanVersion.versionNumber }} 已锁定</strong>
                      </div>
                      <span class="agent-version-badge">已锁定</span>
                    </div>
                    <dl>
                      <div>
                        <dt>方案</dt>
                        <dd>{{ runPlanVersion.planCode }}</dd>
                      </div>
                      <div>
                        <dt>锁定时间</dt>
                        <dd>{{ formatDateTime(runPlanVersion.lockedAt) }}</dd>
                      </div>
                      <div>
                        <dt>锁定身份</dt>
                        <dd>{{ runPlanVersion.lockedByDisplayName }}</dd>
                      </div>
                      <div>
                        <dt>方案指纹</dt>
                        <dd><code>{{ shortHash(runPlanVersion.planSnapshotHash) }}</code></dd>
                      </div>
                    </dl>
                    <p>
                      该版本已经固定，不会被后续任务修改或重新规划覆盖。导出和现场执行应
                      使用这一正式版本。
                    </p>
                  </template>

                  <template v-else>
                    <div class="agent-publish-heading">
                      <div>
                        <span>正式发布</span>
                        <strong>把候选方案发布为正式版本</strong>
                      </div>
                      <span class="agent-version-badge pending">
                        待生成 V{{ (currentPlanVersion?.versionNumber ?? 0) + 1 }}
                      </span>
                    </div>
                    <p>
                      发布后将固定当前输入、人工约束和完整箱体坐标；后续调整必须形成新的
                      正式版本，不能直接覆盖。
                    </p>
                    <label class="form-field">
                      <span>正式锁定原因</span>
                      <textarea
                        v-model="lockReason"
                        maxlength="2000"
                        placeholder="说明为什么可以发布为仓库作业版本"
                      ></textarea>
                    </label>
                    <small class="agent-demo-identity">
                      发布操作会记录服务端识别到的操作身份、时间和审计信息。
                    </small>
                    <button
                      class="primary-button"
                      type="button"
                      :disabled="locking || planVersionsLoading || !canLock"
                      @click="lockApprovedRun"
                    >
                      {{ locking ? '正在校验并锁定…' : '锁定为正式版本' }}
                    </button>
                  </template>
                </div>
              </aside>
            </section>

            <section
              v-if="planInsightLoading || planInsightError || visiblePlanInsight"
              class="agent-insight-panel"
              aria-live="polite"
            >
              <div class="agent-insight-heading">
                <div>
                  <span>辅助研判</span>
                  <h3>当前方案智能解读</h3>
                  <p>用于快速理解方案，不参与评分，也不会改变审批和发布结果。</p>
                </div>
                <div v-if="visiblePlanInsight" class="agent-insight-meta">
                  <span :data-degraded="visiblePlanInsight.degraded">
                    {{ insightProviderLabel[visiblePlanInsight.provider] }}
                    <template v-if="visiblePlanInsight.degraded"> · 已降级</template>
                  </span>
                  <small>{{ formatDateTime(visiblePlanInsight.generatedAt) }}</small>
                </div>
              </div>

              <div v-if="planInsightLoading" class="agent-insight-loading">
                <span class="loading-ring"></span>
                <p>正在结合当前方案指标生成业务解读，不影响你继续查看 3D 与规则结果。</p>
              </div>

              <div v-else-if="planInsightError" class="agent-insight-error" role="status">
                <UiIcon name="alert" />
                <div>
                  <strong>本次解读未生成</strong>
                  <p>{{ planInsightError }}</p>
                </div>
                <button type="button" @click="generatePlanInsight">重新尝试</button>
              </div>

              <template v-else-if="visiblePlanInsight">
                <div class="agent-insight-summary">
                  <div class="agent-insight-section-title">
                    <UiIcon name="file" />
                    <strong>方案摘要</strong>
                  </div>
                  <p>{{ visiblePlanInsight.summary }}</p>
                </div>

                <div class="agent-insight-grid">
                  <article>
                    <div class="agent-insight-section-title">
                      <UiIcon name="layers" />
                      <strong>方案取舍</strong>
                    </div>
                    <ul v-if="visiblePlanInsight.tradeoffs.length">
                      <li v-for="item in visiblePlanInsight.tradeoffs" :key="item">
                        {{ item }}
                      </li>
                    </ul>
                    <p v-else>暂无额外取舍说明。</p>
                  </article>

                  <article class="risk">
                    <div class="agent-insight-section-title">
                      <UiIcon name="alert" />
                      <strong>风险关注</strong>
                    </div>
                    <ul v-if="visiblePlanInsight.risks.length">
                      <li v-for="item in visiblePlanInsight.risks" :key="item">
                        {{ item }}
                      </li>
                    </ul>
                    <p v-else>未补充新的风险提示，仍需执行现有规则检查。</p>
                  </article>

                  <article class="check">
                    <div class="agent-insight-section-title">
                      <UiIcon name="check" />
                      <strong>下一步复核</strong>
                    </div>
                    <ul v-if="visiblePlanInsight.nextChecks.length">
                      <li v-for="item in visiblePlanInsight.nextChecks" :key="item">
                        {{ item }}
                      </li>
                    </ul>
                    <p v-else>按现有审批流程继续人工复核。</p>
                  </article>
                </div>

                <p class="agent-insight-disclaimer">
                  <UiIcon name="shield" />
                  {{ visiblePlanInsight.disclaimer }}
                </p>
              </template>
            </section>
          </template>

          <section
            v-if="planVersions.length > 0"
            class="agent-version-history-panel"
            aria-label="正式装载方案版本历史"
          >
            <div class="agent-section-heading">
              <div>
                <span>版本记录</span>
                <h3>正式方案版本历史</h3>
                <p>
                  新版本不会覆盖旧版本；每次发布都会保留当时的方案、约束和审批记录。
                </p>
              </div>
              <span class="agent-step-number">03</span>
            </div>

            <div class="agent-version-list">
              <article
                v-for="(version, index) in planVersions"
                :key="version.versionId"
                class="agent-version-item"
                :class="{ current: index === 0 }"
              >
                <div class="agent-version-item-heading">
                  <div>
                    <span>V{{ version.versionNumber }}</span>
                    <strong>{{ version.planCode }}</strong>
                  </div>
                  <span>{{ index === 0 ? '当前正式版本' : '历史正式版本' }}</span>
                </div>
                <dl>
                  <div>
                    <dt>规划引擎</dt>
                    <dd>{{ version.algorithmVersion }}</dd>
                  </div>
                  <div>
                    <dt>评分规则</dt>
                    <dd>{{ version.scoreVersion }}</dd>
                  </div>
                  <div>
                    <dt>批准身份</dt>
                    <dd>{{ version.approvalActorDisplayName }}</dd>
                  </div>
                  <div>
                    <dt>锁定身份</dt>
                    <dd>{{ version.lockedByDisplayName }}</dd>
                  </div>
                  <div>
                    <dt>锁定时间</dt>
                    <dd>{{ formatDateTime(version.lockedAt) }}</dd>
                  </div>
                  <div>
                    <dt>方案指纹</dt>
                    <dd><code>{{ shortHash(version.planSnapshotHash) }}</code></dd>
                  </div>
                </dl>
                <p>{{ version.lockReason }}</p>
                <small>
                  审计编号 · {{ version.versionId }}
                  <template v-if="version.supersedesVersionId">
                    · 替代版本 {{ version.supersedesVersionId }}
                  </template>
                </small>
              </article>
            </div>
          </section>

          <section
            v-if="run && (run.status === 'REJECTED' || run.status === 'FAILED')"
            class="agent-replan-panel"
          >
            <div>
              <strong>重新生成装载方案</strong>
              <p>
                上一轮记录会保留。重新规划会沿用已经确认的约束并建立新的规划批次；如果
                货物尺寸、重量或数量已经变化，请先更新任务主数据。
              </p>
            </div>
            <label class="form-field">
              <span>重新规划原因</span>
              <input
                v-model="replanReason"
                maxlength="2000"
                placeholder="例如：现场希望优先比较利用率方案"
              />
            </label>
            <button class="secondary-button" type="button" :disabled="loading" @click="replanRun">
              {{ loading ? '正在创建新运行…' : '按当前约束重新规划' }}
            </button>
          </section>

          <section
            v-if="run && ['APPROVED', 'REJECTED', 'FAILED'].includes(run.status)"
            class="agent-new-run-panel"
          >
            <div>
              <strong>需要再做一次独立试算？</strong>
              <p>
                新试算会建立独立规划批次；已经批准、驳回或锁定的历史记录不会被覆盖。
              </p>
            </div>
            <button
              class="secondary-button"
              type="button"
              :disabled="loading || locking"
              @click="startRun"
            >
              {{ loading ? '正在创建…' : '发起新试算' }}
            </button>
          </section>
        </main>
      </div>
    </section>
  </div>
</template>
