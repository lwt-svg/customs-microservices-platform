<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  getActiveShipmentAgentRun,
  getShipmentAgentRunHistory,
  reconcileShipmentAgentRun,
  resumeShipmentAgentRun,
  startShipmentAgentRun,
  type ResumeShipmentAgentRunInput,
  type ShipmentAgentRun,
  type ShipmentAgentRunStatus,
  type ShipmentAgentStage,
  type ShipmentCaseStatus,
} from '@/api/platform'
import ShipmentAgentInterruptCard from '@/components/shipment-agent/ShipmentAgentInterruptCard.vue'
import ShipmentAgentTrace from '@/components/shipment-agent/ShipmentAgentTrace.vue'
import ShipmentAgentWorkstreams from '@/components/shipment-agent/ShipmentAgentWorkstreams.vue'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  caseNumber: string
  caseRevision: number
  caseStatus: ShipmentCaseStatus
}>()

const emit = defineEmits<{
  caseChanged: []
}>()

interface PipelineStep {
  code: string
  label: string
  state: 'done' | 'current' | 'pending' | 'stopped'
}

const run = ref<ShipmentAgentRun | null>(null)
const history = ref<ShipmentAgentRun[]>([])
const loading = ref(true)
const activeStateConfirmed = ref(false)
const actionBusy = ref(false)
const loadError = ref('')
const actionError = ref('')
const lastRefreshedAt = ref<Date | null>(null)

// 网络结果不明确时，同一个页面重试必须复用原 key。成功后才清除，避免重复创建运行或
// 重放外部副作用；刷新页面后服务端命令台账仍是最终防线。
const pendingStartKeys = new Map<string, string>()
const pendingReconcileKeys = new Map<string, string>()
const pendingResumeKeys = new Map<string, string>()

const statusLabels: Record<ShipmentAgentRunStatus, string> = {
  RUNNING: '正在编排',
  WAITING_HUMAN: '等待人工',
  WAITING_RESOURCE: '等待专业处理',
  WAITING_EXTERNAL: '等待外部系统',
  COMPLETED: '运行完成',
  FAILED: '运行失败',
  CANCELLED: '已停止',
}

const stageCopy: Record<
  ShipmentAgentStage,
  { eyebrow: string; title: string; description: string }
> = {
  SYNCING_CASE: {
    eyebrow: '自动同步',
    title: '正在同步案件事实',
    description: '重新读取案件、装载和 LINK 权威状态，再决定下一节点。',
  },
  AWAITING_SCENARIO_POLICY: {
    eyebrow: '等待业务确认',
    title: '需要确认本票能力范围',
    description: '当前运输场景没有预设固定策略，系统不会自行猜测是否需要装载或订舱。',
  },
  ASSESSING_DATA_READINESS: {
    eyebrow: '自动检查',
    title: '正在检查货物与库存数据',
    description: '核对可用库存、箱规、托盘主数据和证据时效，发现缺口会生成业务补数任务。',
  },
  AWAITING_DATA_REMEDIATION: {
    eyebrow: '等待业务补数',
    title: '数据尚未达到执行条件',
    description: '请补充库存或核对主数据；提交后系统会自动回到本步骤重新评估。',
  },
  AWAITING_RESOURCE_BINDING: {
    eyebrow: '等待资源准备',
    title: '等待准备并绑定专业资源',
    description: '请先在专业工作台创建真实任务，再回到案件建立稳定编号关联。',
  },
  AWAITING_RESOURCE_SELECTION: {
    eyebrow: '等待业务选择',
    title: '同类资源不止一条',
    description: '总 Agent 已暂停，等待人工选择本次运行真正使用的业务资源。',
  },
  AWAITING_CASE_ACTIVATION: {
    eyebrow: '等待业务批准',
    title: '等待批准激活案件',
    description: '案件仍是草稿；进入正式作业前需要明确人工批准。',
  },
  STARTING_LOAD_AGENT: {
    eyebrow: '自动执行',
    title: '正在启动装载子 Agent',
    description: '使用确定性幂等键启动已绑定任务，不会创建第二条重复运行。',
  },
  AWAITING_LOAD_CONSTRAINTS: {
    eyebrow: '等待装载资料',
    title: '装载 Agent 等待补充约束',
    description: '请在装载工作台填写缺失参数，完成后点击“重新检查”。',
  },
  AWAITING_LOAD_REVIEW: {
    eyebrow: '等待方案复核',
    title: '装载方案等待 3D 复核',
    description: '候选方案已经生成，业务人员需要在专业工作台审批或驳回。',
  },
  AWAITING_LOAD_RELEASE: {
    eyebrow: '等待正式发布',
    title: '等待发布正式装载版本',
    description: '方案已批准但尚未锁定，现场执行前仍需人工正式发布。',
  },
  AWAITING_LOAD_REPLAN: {
    eyebrow: '装载异常待处理',
    title: '装载支线需要重新规划',
    description: '上一轮已驳回或失败；处理完成后由总 Agent 重新观察。',
  },
  AWAITING_BOOKING_SUBMIT_APPROVAL: {
    eyebrow: '等待提交批准',
    title: 'LINK 草稿等待提交批准',
    description: '向外部系统提交属于有副作用动作，必须由业务人员核对后批准。',
  },
  SUBMITTING_BOOKING: {
    eyebrow: '自动提交',
    title: '正在提交 LINK 申请',
    description: '系统先检查当前状态，再执行一次安全提交；结果不明时会先查询而非盲重试。',
  },
  AWAITING_BOOKING_RESULT: {
    eyebrow: '等待外部结果',
    title: '等待承运人异步结果',
    description: '当前不会占用请求线程；收到回调后，重新检查即可恢复流程。',
  },
  AWAITING_BOOKING_RETRY_APPROVAL: {
    eyebrow: '等待恢复批准',
    title: '承运人投递等待人工重试',
    description: '系统保留原幂等身份和尝试次数，批准后才会执行恢复动作。',
  },
  AWAITING_BOOKING_REPLACEMENT: {
    eyebrow: '订舱异常待处理',
    title: '等待替换被拒绝或取消的申请',
    description: '请准备新的 LINK 申请并绑定到案件；系统不会覆盖原失败证据。',
  },
  AWAITING_COMPLETION_APPROVAL: {
    eyebrow: '等待结案确认',
    title: '所有必需支线已经就绪',
    description: '总 Agent 已完成自动检查，等待业务人员确认结案。',
  },
  COMPLETING_CASE: {
    eyebrow: '正在结案',
    title: '正在执行结案命令',
    description: '使用固定幂等键推进案件终态并写入审计记录。',
  },
  COMPLETED: {
    eyebrow: 'COMPLETED',
    title: 'Shipment Agent 闭环已完成',
    description: '装载、订舱和最终人工确认已在同一案件轨迹中形成记录。',
  },
  FAILED: {
    eyebrow: 'RUN FAILED',
    title: '总 Agent 运行失败',
    description: '运行已停止，失败代码和事件轨迹可用于排查与恢复。',
  },
  CANCELLED: {
    eyebrow: 'RUN STOPPED',
    title: '本次总 Agent 已停止',
    description: '历史轨迹仍保留；如业务继续，可按案件当前事实启动新运行。',
  },
}

const currentCopy = computed(() =>
  run.value
    ? stageCopy[run.value.currentStage]
    : {
        eyebrow: 'READY',
        title: '尚未启动 Shipment Agent',
        description: '启动后将编排已绑定的装载任务与 LINK 申请，并在关键节点等待人工。',
      },
)

const pipeline = computed<PipelineStep[]>(() => {
  // 案件没有“活动运行”时，仍用最近一次终态运行还原流程结果。否则一个已经完成的案件
  // 会错误地重新显示“范围策略进行中”，失败运行也会被误画成六步全部完成。
  const pipelineRun = run.value ?? history.value[0] ?? null
  if (!pipelineRun) {
    return [
      { code: '01', label: '范围策略', state: 'current' },
      { code: '02', label: '数据准备', state: 'pending' },
      { code: '03', label: '资源与激活', state: 'pending' },
      { code: '04', label: '智能装载', state: 'pending' },
      { code: '05', label: 'LINK 订舱', state: 'pending' },
      { code: '06', label: '人工结案', state: 'pending' },
    ]
  }

  const stageIndex: Record<ShipmentAgentStage, number> = {
    SYNCING_CASE: 0,
    AWAITING_SCENARIO_POLICY: 0,
    ASSESSING_DATA_READINESS: 1,
    AWAITING_DATA_REMEDIATION: 1,
    AWAITING_RESOURCE_BINDING: 2,
    AWAITING_RESOURCE_SELECTION: 2,
    AWAITING_CASE_ACTIVATION: 2,
    STARTING_LOAD_AGENT: 3,
    AWAITING_LOAD_CONSTRAINTS: 3,
    AWAITING_LOAD_REVIEW: 3,
    AWAITING_LOAD_RELEASE: 3,
    AWAITING_LOAD_REPLAN: 3,
    AWAITING_BOOKING_SUBMIT_APPROVAL: 4,
    SUBMITTING_BOOKING: 4,
    AWAITING_BOOKING_RESULT: 4,
    AWAITING_BOOKING_RETRY_APPROVAL: 4,
    AWAITING_BOOKING_REPLACEMENT: 4,
    AWAITING_COMPLETION_APPROVAL: 5,
    COMPLETING_CASE: 5,
    COMPLETED: 6,
    FAILED: 6,
    CANCELLED: 6,
  }
  const stopped = ['FAILED', 'CANCELLED'].includes(pipelineRun.status)
  const completed = pipelineRun.status === 'COMPLETED'
  const lastBusinessStage = stopped
    ? [...pipelineRun.events]
        .reverse()
        .map((event) => event.stage)
        .find((stage) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(stage))
    : undefined
  const currentIndex = stopped
    ? stageIndex[lastBusinessStage ?? 'SYNCING_CASE']
    : stageIndex[pipelineRun.currentStage]

  return ['范围策略', '数据准备', '资源与激活', '智能装载', 'LINK 订舱', '人工结案'].map(
    (label, index) => ({
      code: String(index + 1).padStart(2, '0'),
      label,
      state: completed
        ? 'done'
        : stopped && index >= currentIndex
        ? 'stopped'
        : index < currentIndex
          ? 'done'
          : index === currentIndex
            ? 'current'
            : 'pending',
    }),
  )
})

const canStart = computed(
  () =>
    !run.value &&
    !loading.value &&
    activeStateConfirmed.value &&
    !['COMPLETED', 'CANCELLED'].includes(props.caseStatus),
)

const automaticallyRoutableStages = new Set<ShipmentAgentStage>([
  'SYNCING_CASE',
  'ASSESSING_DATA_READINESS',
  'STARTING_LOAD_AGENT',
  'AWAITING_BOOKING_RESULT',
  'AWAITING_CASE_ACTIVATION',
  'SUBMITTING_BOOKING',
  'AWAITING_BOOKING_RETRY_APPROVAL',
  'COMPLETING_CASE',
])

// 正常请求会一直推进到人工、专业系统或外部等待点；若页面看见无 interrupt 的
// RUNNING 自动阶段，通常表示数据库命令已提交，但进程在 LangGraph 续跑前中断。
// 此时允许显式 kick，后端会登记幂等 NO_OP 并从权威阶段继续。
const isAutomaticRecoveryState = computed(
  () => {
    const current = run.value
    return (
      current !== null &&
      current.status === 'RUNNING' &&
      current.pendingInterrupt === null &&
      automaticallyRoutableStages.has(current.currentStage)
    )
  },
)

const canReconcile = computed(
  () =>
    Boolean(run.value) &&
    !run.value?.pendingInterrupt &&
    (Boolean(run.value?.allowedActions.includes('RECHECK')) ||
      isAutomaticRecoveryState.value) &&
    !['COMPLETED', 'FAILED', 'CANCELLED'].includes(run.value?.status ?? ''),
)

const lastRun = computed(() => history.value[0] ?? null)

const readinessIssueCount = computed(() => {
  const readiness = run.value?.dataReadiness
  return readiness?.issueCount ?? readiness?.issueCounts?.total ?? readiness?.issues.length ?? 0
})

const readinessBlockingCount = computed(() => {
  const readiness = run.value?.dataReadiness
  return (
    readiness?.blockingIssueCount ??
    readiness?.issueCounts?.blocking ??
    readiness?.issues.length ??
    0
  )
})

const readinessStatusLabel = computed(() => {
  const status = run.value?.dataReadiness?.status
  const labels: Record<string, string> = {
    LEGACY_BYPASSED: '历史运行未启用',
    NOT_EVALUATED: '等待检查',
    ACTION_REQUIRED: '需要补充数据',
    READY: '业务数据已就绪',
    DEMO_READY: '演示数据已就绪',
  }
  return status ? labels[status] ?? status : '尚未评估'
})

const readinessTone = computed(() => {
  const status = run.value?.dataReadiness?.status
  if (status === 'READY') return 'ready'
  if (status === 'DEMO_READY' || status === 'LEGACY_BYPASSED') return 'demo'
  if (status === 'ACTION_REQUIRED') return 'attention'
  return 'pending'
})

function readinessSourceLabel(value: string | undefined): string {
  const labels: Record<string, string> = {
    NOT_APPLICABLE: '当前策略不适用',
    OPERATIONAL: '业务数据',
    DEMO: '脱敏演示数据',
  }
  return value ? labels[value] ?? value : '尚未确认'
}

function readinessCheckedAt(): string | null {
  const readiness = run.value?.dataReadiness
  return readiness?.assessedAt ?? readiness?.checkedAt ?? null
}

function formatDateTime(value: string | null): string {
  if (!value) return '尚未检查'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '尚未检查'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

function createIdempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${random}`
}

async function loadAgent(): Promise<void> {
  loading.value = true
  activeStateConfirmed.value = false
  loadError.value = ''
  try {
    const [activeResult, historyResult] = await Promise.allSettled([
      getActiveShipmentAgentRun(props.caseNumber),
      getShipmentAgentRunHistory(props.caseNumber),
    ])

    if (activeResult.status === 'fulfilled') {
      run.value = activeResult.value
      activeStateConfirmed.value = true
    } else {
      throw activeResult.reason
    }

    if (historyResult.status === 'fulfilled') {
      history.value = historyResult.value
    } else {
      throw historyResult.reason
    }
    lastRefreshedAt.value = new Date()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取 Shipment Agent 失败'
  } finally {
    loading.value = false
  }
}

async function startAgent(): Promise<void> {
  if (!canStart.value) return
  actionBusy.value = true
  actionError.value = ''
  const commandScope = `${props.caseNumber}:r${props.caseRevision}`
  let idempotencyKey = pendingStartKeys.get(commandScope)
  if (!idempotencyKey) {
    idempotencyKey = createIdempotencyKey(`shipment-agent-start-${props.caseNumber}`)
    pendingStartKeys.set(commandScope, idempotencyKey)
  }
  try {
    const created = await startShipmentAgentRun(
      props.caseNumber,
      { expectedCaseRevision: props.caseRevision },
      idempotencyKey,
    )
    pendingStartKeys.delete(commandScope)
    run.value = created
    history.value = [created, ...history.value.filter((item) => item.runId !== created.runId)]
    lastRefreshedAt.value = new Date()
    emit('caseChanged')
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '启动 Shipment Agent 失败'
  } finally {
    actionBusy.value = false
  }
}

async function reconcile(): Promise<void> {
  const current = run.value
  if (!current || !canReconcile.value) return
  actionBusy.value = true
  actionError.value = ''
  const commandScope = `${current.runId}:r${current.runVersion}`
  let idempotencyKey = pendingReconcileKeys.get(commandScope)
  if (!idempotencyKey) {
    idempotencyKey = createIdempotencyKey(
      `shipment-agent-reconcile-${current.runId}-r${current.runVersion}`,
    )
    pendingReconcileKeys.set(commandScope, idempotencyKey)
  }
  try {
    const changed = await reconcileShipmentAgentRun(
      current.runId,
      { expectedRunVersion: current.runVersion },
      idempotencyKey,
    )
    pendingReconcileKeys.delete(commandScope)
    replaceRun(changed)
    emit('caseChanged')
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '重新检查失败'
  } finally {
    actionBusy.value = false
  }
}

async function resume(payload: ResumeShipmentAgentRunInput): Promise<void> {
  const current = run.value
  if (!current) return
  actionBusy.value = true
  actionError.value = ''
  const commandScope = `${current.runId}:${payload.expectedRunVersion}:${payload.interruptType}:${payload.action}`
  let idempotencyKey = pendingResumeKeys.get(commandScope)
  if (!idempotencyKey) {
    idempotencyKey = createIdempotencyKey('shipment-agent-resume')
    pendingResumeKeys.set(commandScope, idempotencyKey)
  }

  try {
    const changed = await resumeShipmentAgentRun(current.runId, payload, idempotencyKey)
    pendingResumeKeys.delete(commandScope)
    replaceRun(changed)
    emit('caseChanged')
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : '提交人工动作失败'
  } finally {
    actionBusy.value = false
  }
}

function replaceRun(changed: ShipmentAgentRun): void {
  run.value = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(changed.status)
    ? null
    : changed
  history.value = [
    changed,
    ...history.value.filter((item) => item.runId !== changed.runId),
  ]
  lastRefreshedAt.value = new Date()
}

function formatRefreshTime(value: Date | null): string {
  if (!value) return '尚未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(value)
}

watch(
  () => props.caseNumber,
  () => {
    run.value = null
    history.value = []
    activeStateConfirmed.value = false
    pendingStartKeys.clear()
    pendingReconcileKeys.clear()
    pendingResumeKeys.clear()
    void loadAgent()
  },
)

onMounted(loadAgent)
</script>

<template>
  <section class="shipment-agent-panel">
    <header class="agent-command-header">
      <div class="agent-command-identity">
        <span class="agent-orb"><UiIcon name="server" /></span>
        <div>
          <span>智能协同作业</span>
          <h3>发运智能协同</h3>
          <p>自动检查业务数据并协调装载与订舱；遇到缺失、冲突或高风险动作时交给业务人员确认。</p>
        </div>
      </div>

      <div class="agent-command-actions">
        <div>
          <span>最近同步</span>
          <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
        </div>
        <button type="button" :disabled="loading || actionBusy" @click="loadAgent">
          <UiIcon name="refresh" />
          {{ loading ? '同步中' : '刷新' }}
        </button>
        <RouterLink to="/tradeops/agents">
          运行中心
          <UiIcon name="arrow" />
        </RouterLink>
      </div>
    </header>

    <div v-if="loadError" class="agent-inline-error" role="alert">
      <strong>Agent 状态读取失败</strong>
      <span>{{ loadError }}</span>
      <button type="button" @click="loadAgent">重试</button>
    </div>

    <div v-if="loading && !run && history.length === 0" class="agent-panel-loading">
      <span></span>
      <div>
        <strong>正在读取总 Agent 状态</strong>
        <p>GET 只读取事实，不会隐式启动、提交或推进流程。</p>
      </div>
    </div>

    <template v-else>
      <div class="agent-pipeline" aria-label="Shipment Agent 计划">
        <div
          v-for="step in pipeline"
          :key="step.code"
          class="agent-pipeline-step"
          :data-state="step.state"
        >
          <span>
            <UiIcon v-if="step.state === 'done'" name="check" />
            <template v-else>{{ step.code }}</template>
          </span>
          <strong>{{ step.label }}</strong>
        </div>
      </div>

      <section v-if="run" class="agent-current-state" :data-status="run.status">
        <div class="agent-state-copy">
          <span>{{ currentCopy.eyebrow }}</span>
          <h4>{{ currentCopy.title }}</h4>
          <p>{{ currentCopy.description }}</p>
        </div>
        <div class="agent-state-facts">
          <div>
            <span>自动作业状态</span>
            <strong>{{ statusLabels[run.status] }}</strong>
          </div>
          <div>
            <span>数据准备</span>
            <strong>{{ readinessStatusLabel }}</strong>
          </div>
          <div>
            <span>阻塞问题</span>
            <strong>{{ readinessBlockingCount }} 项</strong>
          </div>
          <div>
            <span>下一步</span>
            <strong>{{ currentCopy.title }}</strong>
          </div>
        </div>
      </section>

      <section
        v-if="run?.dataReadiness"
        class="data-readiness-board"
        :data-tone="readinessTone"
      >
        <div class="readiness-scorecard">
          <span class="readiness-icon">
            <UiIcon :name="readinessTone === 'ready' ? 'check' : 'alert'" />
          </span>
          <div>
            <span>DATA READINESS</span>
            <h4>{{ readinessStatusLabel }}</h4>
            <p>
              {{
                readinessTone === 'ready'
                  ? '库存与主数据已通过当前策略检查，可以继续执行专业支线。'
                  : readinessTone === 'demo'
                    ? '当前使用演示或兼容模式，页面会明确保留数据来源，不冒充生产真值。'
                    : readinessTone === 'attention'
                      ? '系统已停止向下执行，请先补充缺失库存或确认主数据。'
                      : '系统将在进入装载和订舱前先核对业务数据。'
              }}
            </p>
          </div>
          <span class="readiness-status-pill">{{ readinessStatusLabel }}</span>
        </div>

        <div class="readiness-metrics">
          <div>
            <span>问题总数</span>
            <strong>{{ readinessIssueCount }}</strong>
          </div>
          <div>
            <span>阻塞问题</span>
            <strong>{{ readinessBlockingCount }}</strong>
          </div>
          <div>
            <span>数据模式</span>
            <strong>{{ readinessSourceLabel(run.dataReadiness.sourceMode) }}</strong>
          </div>
          <div>
            <span>最近检查</span>
            <strong>{{ formatDateTime(readinessCheckedAt()) }}</strong>
          </div>
        </div>

        <div v-if="run.dataReadiness.issues.length" class="readiness-issue-summary">
          <article v-for="issue in run.dataReadiness.issues.slice(0, 4)" :key="issue.issueId">
            <span><UiIcon name="alert" /></span>
            <div>
              <strong>{{ issue.subjectCode }}</strong>
              <p>{{ issue.message }}</p>
            </div>
            <small>{{ issue.ownerRole === 'WAREHOUSE_OPERATOR' ? '仓库业务员' : issue.ownerRole }}</small>
          </article>
          <p v-if="run.dataReadiness.issues.length > 4" class="readiness-more">
            另有 {{ run.dataReadiness.issues.length - 4 }} 项问题，可在下方补数任务中统一处理。
          </p>
        </div>
      </section>

      <section v-else class="agent-start-card">
        <span class="agent-start-icon"><UiIcon name="layers" /></span>
        <div>
          <span>{{ lastRun ? 'NO ACTIVE RUN' : 'READY TO START' }}</span>
          <h4>
            {{
              lastRun?.status === 'COMPLETED'
                ? '最近一次智能协同已经完成'
                : lastRun
                  ? '当前没有活动运行'
                  : '让一票案件真正进入 Agent 闭环'
            }}
          </h4>
          <p v-if="lastRun">
            最近一次运行状态为 {{ statusLabels[lastRun.status] }}，历史 Trace 保留在运行中心。
            如案件仍需继续，可基于当前 revision 启动新运行。
          </p>
          <p v-else>
            Agent 会先读取案件场景与已绑定资源；缺少资料时安全暂停，不会凭空创建任务，
            也不会绕过 LINK 提交和结案审批。
          </p>
        </div>
        <button v-if="canStart" type="button" :disabled="actionBusy" @click="startAgent">
          <UiIcon name="plus" />
          {{ actionBusy ? '正在启动…' : '启动总 Agent' }}
        </button>
      </section>

      <div v-if="actionError && !run?.pendingInterrupt" class="agent-action-error" role="alert">
        {{ actionError }}
      </div>

      <template v-if="run">
        <ShipmentAgentInterruptCard
          v-if="run.pendingInterrupt"
          :run="run"
          :busy="actionBusy"
          :error="actionError"
          @submit="resume"
        />

        <section
          v-else-if="canReconcile"
          class="agent-wait-card"
          :data-status="run.status"
        >
          <span><UiIcon :name="run.status === 'WAITING_EXTERNAL' ? 'clock' : 'layers'" /></span>
          <div>
            <strong>
              {{
                isAutomaticRecoveryState
                  ? '自动执行已提交，正在等待恢复'
                  : run.status === 'WAITING_EXTERNAL'
                  ? '当前正在等待外部异步状态'
                  : '请先完成专业工作台中的待办'
              }}
            </strong>
            <p>
              {{
                isAutomaticRecoveryState
                  ? '可从数据库记录的当前阶段恢复自动执行；系统会先同步权威状态，幂等保护会避免重复补数或重复提交。'
                  : '处理完成或承运人回调后，点击重新检查；系统会从当前暂停点重新读取权威状态，已完成的步骤不会重复提交。'
              }}
            </p>
          </div>
          <button type="button" :disabled="actionBusy" @click="reconcile">
            <UiIcon name="refresh" />
            {{
              actionBusy
                ? '检查中…'
                : isAutomaticRecoveryState
                  ? '恢复自动执行 / 重新检查'
                  : '重新检查并继续'
            }}
          </button>
        </section>

        <section class="agent-detail-main">
          <ShipmentAgentWorkstreams :run="run" />
        </section>

        <details class="agent-technical-details">
          <summary>
            <span>
              <UiIcon name="server" />
              Agent 技术详情与运行轨迹
            </span>
            <small>默认收起，供开发、审计和故障排查使用</small>
          </summary>
          <div class="technical-fact-grid">
            <div><span>Run ID</span><code>{{ run.runId }}</code></div>
            <div><span>当前节点</span><code>{{ run.currentStage }}</code></div>
            <div><span>工作流版本</span><code>{{ run.workflowVersion }}</code></div>
            <div><span>策略 / 运行版本</span><code>{{ run.policyVersion }} · R{{ run.runVersion }}</code></div>
          </div>
          <ShipmentAgentTrace :events="run.events" />
        </details>
      </template>

      <details v-else-if="lastRun" class="agent-technical-details">
        <summary>
          <span><UiIcon name="clock" />最近一次 Agent 运行记录</span>
          <small>{{ statusLabels[lastRun.status] }}，点击查看审计轨迹</small>
        </summary>
        <ShipmentAgentTrace :events="lastRun.events" />
      </details>
    </template>
  </section>
</template>

<style scoped>
.shipment-agent-panel {
  display: grid;
  gap: 16px;
  overflow: hidden;
  padding: 21px;
  border: 1px solid #dbe5e1;
  border-radius: 18px;
  background:
    radial-gradient(circle at 95% 0%, rgba(95, 164, 136, 0.08), transparent 29%),
    #fff;
  box-shadow: 0 14px 38px rgba(27, 52, 44, 0.055);
}

.agent-command-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.agent-command-identity {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 13px;
}

.agent-orb {
  display: grid;
  width: 45px;
  height: 45px;
  flex: 0 0 45px;
  place-items: center;
  border-radius: 14px;
  color: #dff3e9;
  background:
    radial-gradient(circle at 34% 28%, #75bd9f, transparent 22%),
    linear-gradient(145deg, #1b332b, #356a58);
  box-shadow: 0 8px 18px rgba(38, 91, 72, 0.2);
}

.agent-orb :deep(.ui-icon) {
  width: 21px;
}

.agent-command-identity > div > span {
  color: #648274;
  font-size: 11px;
  font-weight: 780;
  letter-spacing: 0.15em;
}

.agent-command-identity h3,
.agent-command-identity p {
  margin: 0;
}

.agent-command-identity h3 {
  margin-top: 5px;
  color: #203029;
  font-size: 19px;
}

.agent-command-identity p {
  margin-top: 5px;
  color: #74817c;
  font-size: 12px;
  line-height: 1.55;
}

.agent-command-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
}

.agent-command-actions > div {
  margin-right: 5px;
  text-align: right;
}

.agent-command-actions > div span,
.agent-command-actions > div strong {
  display: block;
}

.agent-command-actions > div span {
  color: #929b97;
  font-size: 11px;
}

.agent-command-actions > div strong {
  margin-top: 3px;
  color: #66736e;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.agent-command-actions button,
.agent-command-actions a {
  display: inline-flex;
  min-height: 34px;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid #dbe3df;
  border-radius: 9px;
  color: #53635d;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}

.agent-command-actions button:disabled {
  cursor: wait;
  opacity: 0.58;
}

.agent-command-actions :deep(.ui-icon) {
  width: 13px;
}

.agent-inline-error,
.agent-action-error {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid #eccccc;
  border-radius: 9px;
  color: #8f4747;
  background: #fff6f6;
  font-size: 12px;
}

.agent-inline-error span {
  flex: 1;
}

.agent-inline-error button {
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  text-decoration: underline;
}

.agent-panel-loading {
  display: flex;
  min-height: 130px;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #6f7c77;
}

.agent-panel-loading > span {
  width: 24px;
  height: 24px;
  border: 3px solid #e1e9e5;
  border-top-color: #4a7b69;
  border-radius: 50%;
  animation: agent-spin 0.75s linear infinite;
}

.agent-panel-loading strong,
.agent-panel-loading p {
  display: block;
  margin: 0;
}

.agent-panel-loading strong {
  font-size: 13px;
}

.agent-panel-loading p {
  margin-top: 4px;
  color: #98a19d;
  font-size: 12px;
}

.agent-pipeline {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid #e4e9e7;
  border-radius: 12px;
  background: #f8faf9;
}

.agent-pipeline-step {
  position: relative;
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
  padding: 10px 11px;
  border-right: 1px solid #e7ebe9;
  color: #8c9692;
}

.agent-pipeline-step:last-child {
  border-right: 0;
}

.agent-pipeline-step > span {
  display: grid;
  width: 21px;
  height: 21px;
  flex: 0 0 21px;
  place-items: center;
  border: 1px solid #dce3e0;
  border-radius: 50%;
  background: #fff;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.agent-pipeline-step > span :deep(.ui-icon) {
  width: 11px;
}

.agent-pipeline-step strong {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-pipeline-step[data-state="done"] {
  color: #3f755f;
  background: #f2f8f5;
}

.agent-pipeline-step[data-state="done"] > span {
  border-color: #95bea9;
  color: #fff;
  background: #4d866d;
}

.agent-pipeline-step[data-state="current"] {
  color: #234d3e;
  background: #e8f2ed;
}

.agent-pipeline-step[data-state="current"] > span {
  border-color: #6e9e89;
  color: #fff;
  background: #356a57;
  box-shadow: 0 0 0 3px rgba(53, 106, 87, 0.1);
}

.agent-pipeline-step[data-state="stopped"] {
  color: #9b6868;
  background: #fcf4f4;
}

.agent-current-state {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(430px, 1fr);
  gap: 18px;
  align-items: center;
  padding: 17px;
  border: 1px solid #dce6e1;
  border-radius: 14px;
  background:
    linear-gradient(100deg, #f4f8f6, #fbfdfc);
}

.agent-state-copy > span {
  color: #628273;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.agent-state-copy h4,
.agent-state-copy p {
  margin: 0;
}

.agent-state-copy h4 {
  margin-top: 6px;
  color: #26352f;
  font-size: 18px;
}

.agent-state-copy p {
  margin-top: 6px;
  color: #6d7a75;
  font-size: 13px;
  line-height: 1.65;
}

.agent-state-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid #e1e7e4;
  border-radius: 10px;
  background: #e1e7e4;
}

.agent-state-facts > div {
  min-width: 0;
  padding: 10px;
  background: rgba(255, 255, 255, 0.9);
}

.agent-state-facts span,
.agent-state-facts strong,
.agent-state-facts code {
  display: block;
}

.agent-state-facts span {
  color: #929c98;
  font-size: 11px;
}

.agent-state-facts strong,
.agent-state-facts code {
  overflow: hidden;
  margin-top: 5px;
  color: #34433d;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-state-facts code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.agent-start-card,
.agent-wait-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 13px;
  align-items: center;
  padding: 17px;
  border: 1px solid #dce6e1;
  border-radius: 14px;
  background: #f7faf8;
}

.agent-start-icon,
.agent-wait-card > span {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #376b59;
  background: #e0eee7;
}

.agent-start-icon :deep(.ui-icon),
.agent-wait-card > span :deep(.ui-icon) {
  width: 18px;
}

.agent-start-card > div > span {
  color: #6d8b7e;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.13em;
}

.agent-start-card h4,
.agent-start-card p,
.agent-wait-card strong,
.agent-wait-card p {
  margin: 0;
}

.agent-start-card h4 {
  margin-top: 5px;
  color: #2d3934;
  font-size: 16px;
}

.agent-start-card p,
.agent-wait-card p {
  margin-top: 5px;
  color: #74807b;
  font-size: 12px;
  line-height: 1.6;
}

.agent-start-card > button,
.agent-wait-card > button {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid #356a58;
  border-radius: 9px;
  color: #fff;
  background: #356a58;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.agent-start-card > button:disabled,
.agent-wait-card > button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.agent-start-card > button :deep(.ui-icon),
.agent-wait-card > button :deep(.ui-icon) {
  width: 13px;
}

.agent-wait-card[data-status="WAITING_EXTERNAL"] {
  border-color: #cfdcec;
  background: #f5f8fc;
}

.agent-wait-card[data-status="WAITING_EXTERNAL"] > span {
  color: #4f7298;
  background: #e5eef8;
}

.agent-wait-card strong {
  color: #33413c;
  font-size: 14px;
}

.agent-detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, 0.8fr);
  gap: 17px;
  align-items: start;
}

.agent-detail-main,
.agent-detail-trace,
.agent-last-trace {
  min-width: 0;
  padding: 16px;
  border: 1px solid #e4e9e7;
  border-radius: 14px;
  background: #fff;
}

.data-readiness-board {
  display: grid;
  gap: 13px;
  padding: 17px;
  border: 1px solid #dfe6e3;
  border-radius: 14px;
  background: #fafcfb;
}

.data-readiness-board[data-tone="ready"] {
  border-color: #bfdccc;
  background: #f3faf6;
}

.data-readiness-board[data-tone="attention"] {
  border-color: #e7d1a7;
  background: #fffaf0;
}

.data-readiness-board[data-tone="demo"] {
  border-color: #cfdaea;
  background: #f5f8fc;
}

.readiness-scorecard {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.readiness-icon {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #6f7f78;
  background: #e8eeeb;
}

[data-tone="ready"] .readiness-icon {
  color: #347055;
  background: #dcefe4;
}

[data-tone="attention"] .readiness-icon {
  color: #92691f;
  background: #f5e7c8;
}

[data-tone="demo"] .readiness-icon {
  color: #4f7298;
  background: #e4edf8;
}

.readiness-icon :deep(.ui-icon) {
  width: 19px;
}

.readiness-scorecard > div > span {
  color: #788882;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.1em;
}

.readiness-scorecard h4,
.readiness-scorecard p {
  margin: 0;
}

.readiness-scorecard h4 {
  margin-top: 4px;
  color: #2f3d37;
  font-size: 16px;
}

.readiness-scorecard p {
  margin-top: 4px;
  color: #71807a;
  font-size: 12px;
  line-height: 1.55;
}

.readiness-status-pill {
  padding: 6px 9px;
  border-radius: 999px;
  color: #5d6c66;
  background: #e9eeec;
  font-size: 11px;
  font-weight: 700;
}

[data-tone="ready"] .readiness-status-pill {
  color: #2e694f;
  background: #dcefe4;
}

[data-tone="attention"] .readiness-status-pill {
  color: #815e20;
  background: #f3e4c3;
}

[data-tone="demo"] .readiness-status-pill {
  color: #45698f;
  background: #e3edf8;
}

.readiness-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.readiness-metrics > div {
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid rgba(217, 226, 222, 0.9);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.78);
}

.readiness-metrics span,
.readiness-metrics strong {
  display: block;
}

.readiness-metrics span {
  color: #89958f;
  font-size: 11px;
}

.readiness-metrics strong {
  overflow: hidden;
  margin-top: 4px;
  color: #38463f;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.readiness-issue-summary {
  display: grid;
  gap: 7px;
}

.readiness-issue-summary article {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 9px;
  align-items: start;
  padding: 10px 11px;
  border: 1px solid #e7d8bc;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.76);
}

.readiness-issue-summary article > span {
  color: #956b25;
}

.readiness-issue-summary article > span :deep(.ui-icon) {
  width: 15px;
}

.readiness-issue-summary strong,
.readiness-issue-summary p {
  display: block;
  margin: 0;
}

.readiness-issue-summary strong {
  color: #4e4434;
  font-size: 12px;
}

.readiness-issue-summary p {
  margin-top: 3px;
  color: #746957;
  font-size: 12px;
  line-height: 1.45;
}

.readiness-issue-summary small {
  color: #8a795c;
  font-size: 11px;
  white-space: nowrap;
}

.readiness-more {
  margin: 1px 0 0 35px;
  color: #8c7b61;
  font-size: 11px;
}

.agent-technical-details {
  overflow: hidden;
  border: 1px solid #e2e8e5;
  border-radius: 13px;
  background: #fbfcfc;
}

.agent-technical-details > summary {
  display: flex;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 15px;
  color: #53615c;
  cursor: pointer;
  list-style: none;
}

.agent-technical-details > summary::-webkit-details-marker {
  display: none;
}

.agent-technical-details > summary > span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 700;
}

.agent-technical-details > summary :deep(.ui-icon) {
  width: 15px;
}

.agent-technical-details > summary small {
  color: #909a96;
  font-size: 11px;
}

.agent-technical-details[open] > summary {
  border-bottom: 1px solid #e5eae8;
  background: #f6f8f7;
}

.agent-technical-details > :not(summary) {
  margin: 15px;
}

.technical-fact-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.technical-fact-grid > div {
  min-width: 0;
  padding: 10px;
  border-radius: 9px;
  background: #f2f5f4;
}

.technical-fact-grid span,
.technical-fact-grid code {
  display: block;
}

.technical-fact-grid span {
  color: #8b9692;
  font-size: 10px;
}

.technical-fact-grid code {
  overflow: hidden;
  margin-top: 4px;
  color: #43514c;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@keyframes agent-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1020px) {
  .agent-command-header {
    align-items: flex-start;
  }

  .agent-command-actions {
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .agent-current-state {
    grid-template-columns: 1fr;
  }

  .agent-detail-grid {
    grid-template-columns: 1fr;
  }

  .readiness-metrics,
  .technical-fact-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .shipment-agent-panel {
    padding: 15px;
  }

  .agent-command-header,
  .agent-command-actions {
    display: grid;
  }

  .agent-command-actions {
    grid-template-columns: repeat(2, auto);
    justify-content: start;
  }

  .agent-command-actions > div {
    grid-column: 1 / -1;
    text-align: left;
  }

  .agent-pipeline {
    grid-template-columns: 1fr;
  }

  .agent-pipeline-step {
    border-right: 0;
    border-bottom: 1px solid #e7ebe9;
  }

  .agent-pipeline-step:last-child {
    border-bottom: 0;
  }

  .agent-state-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .readiness-scorecard,
  .readiness-issue-summary article {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .readiness-status-pill,
  .readiness-issue-summary article > small {
    grid-column: 2;
    justify-self: start;
  }

  .agent-start-card,
  .agent-wait-card {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .agent-start-card > button,
  .agent-wait-card > button {
    grid-column: 2;
    justify-self: start;
  }
}
</style>
