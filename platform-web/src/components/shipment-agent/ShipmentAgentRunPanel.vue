<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

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
  state: 'done' | 'current' | 'pending' | 'stopped' | 'skipped'
}

const run = ref<ShipmentAgentRun | null>(null)
const history = ref<ShipmentAgentRun[]>([])
const loading = ref(true)
const activeStateConfirmed = ref(false)
const actionBusy = ref(false)
const loadError = ref('')
const actionError = ref('')
const lastRefreshedAt = ref<Date | null>(null)
let waitingRefreshTimer: number | undefined

// 网络结果不明确时，同一个页面重试必须复用原 key。成功后才清除，避免重复创建运行或
// 重放外部副作用；刷新页面后服务端命令台账仍是最终防线。
const pendingStartKeys = new Map<string, string>()
const pendingReconcileKeys = new Map<string, string>()
const pendingResumeKeys = new Map<string, string>()

const statusLabels: Record<ShipmentAgentRunStatus, string> = {
  RUNNING: '系统正在处理',
  WAITING_HUMAN: '等待业务处理',
  WAITING_RESOURCE: '等待专业作业',
  WAITING_EXTERNAL: '等待承运人回复',
  COMPLETED: '流程已完成',
  FAILED: '处理失败',
  CANCELLED: '本次处理已停止',
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
    description: '系统已暂停，请选择本次实际使用的业务作业。',
  },
  AWAITING_CASE_ACTIVATION: {
    eyebrow: '等待业务批准',
    title: '等待批准激活案件',
    description: '案件仍是草稿；进入正式作业前需要明确人工批准。',
  },
  STARTING_LOAD_AGENT: {
    eyebrow: '自动执行',
    title: '正在启动智能装载',
    description: '使用确定性幂等键启动已绑定任务，不会创建第二条重复运行。',
  },
  AWAITING_LOAD_CONSTRAINTS: {
    eyebrow: '等待装载资料',
    title: '智能装载等待补充约束',
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
    title: '装载方案需要重新规划',
    description: '上一轮已驳回或失败；处理完成后系统会重新读取最新状态。',
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
    description: '当前不会占用请求线程；系统会自动跟踪回调并恢复流程，立即检查只作故障兜底。',
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
    title: '本票所需专业作业已经就绪',
    description: '系统已完成自动检查，等待业务人员确认结案。',
  },
  COMPLETING_CASE: {
    eyebrow: '正在结案',
    title: '正在执行结案命令',
    description: '使用固定幂等键推进案件终态并写入审计记录。',
  },
  COMPLETED: {
    eyebrow: '流程完成',
    title: '本票发运协同已经完成',
    description: '本票启用的专业作业和最终人工确认已在同一案件轨迹中形成记录。',
  },
  FAILED: {
    eyebrow: '处理失败',
    title: '本次自动处理已经停止',
    description: '运行已停止，失败代码和事件轨迹可用于排查与恢复。',
  },
  CANCELLED: {
    eyebrow: '已停止',
    title: '本次自动处理已停止',
    description: '历史轨迹仍保留；如业务继续，可按案件当前事实启动新运行。',
  },
}

const displayRun = computed(() => run.value ?? history.value[0] ?? null)

const openNotifications = computed(() => {
  const severityOrder = { ERROR: 0, WARNING: 1, INFO: 2 }
  return [...(displayRun.value?.notifications ?? [])]
    .filter((notification) => notification.status === 'OPEN')
    .sort(
      (left, right) =>
        severityOrder[left.severity] - severityOrder[right.severity] ||
        new Date(right.lastSeenAt).getTime() - new Date(left.lastSeenAt).getTime(),
    )
})

const currentCopy = computed(() =>
  displayRun.value
    ? stageCopy[displayRun.value.currentStage]
    : {
        eyebrow: '等待开始',
        title: '尚未开始自动处理',
        description: '开始后，系统会先核验资料与库存，再按顺序推进装载、订舱和结案。',
      },
)

const businessStageIndex: Record<ShipmentAgentStage, number> = {
  SYNCING_CASE: 0,
  AWAITING_SCENARIO_POLICY: 0,
  ASSESSING_DATA_READINESS: 0,
  AWAITING_DATA_REMEDIATION: 0,
  AWAITING_RESOURCE_BINDING: 0,
  AWAITING_RESOURCE_SELECTION: 0,
  AWAITING_CASE_ACTIVATION: 0,
  STARTING_LOAD_AGENT: 1,
  AWAITING_LOAD_CONSTRAINTS: 1,
  AWAITING_LOAD_REVIEW: 1,
  AWAITING_LOAD_RELEASE: 1,
  AWAITING_LOAD_REPLAN: 1,
  AWAITING_BOOKING_SUBMIT_APPROVAL: 2,
  SUBMITTING_BOOKING: 2,
  AWAITING_BOOKING_RESULT: 3,
  AWAITING_BOOKING_RETRY_APPROVAL: 3,
  // 被拒绝或取消后仍要回到 LINK 申请本身处理，不能误画成“承运人确认”。
  AWAITING_BOOKING_REPLACEMENT: 2,
  AWAITING_COMPLETION_APPROVAL: 4,
  COMPLETING_CASE: 4,
  COMPLETED: 5,
  FAILED: 5,
  CANCELLED: 5,
}

const businessStageLabels = [
  '资料与库存核验',
  '智能装载',
  'LINK 订舱',
  '承运人确认',
  '结案',
]

function isBusinessStageRequired(
  runSnapshot: ShipmentAgentRun,
  index: number,
): boolean {
  // 能力策略尚未确认时，空数组表示“还没决定”，不能提前显示成“不适用”。
  if (!runSnapshot.scenarioPolicyConfirmed) {
    return true
  }
  if (index === 1) {
    return runSnapshot.requiredCapabilities.includes('LOAD_PLANNING')
  }
  if (index === 2 || index === 3) {
    return runSnapshot.requiredCapabilities.includes('LINK_BOOKING')
  }
  return true
}

const stageOwnerLabels: Record<ShipmentAgentStage, string> = {
  SYNCING_CASE: '系统自动处理',
  AWAITING_SCENARIO_POLICY: '发运协调员',
  ASSESSING_DATA_READINESS: '系统自动处理',
  AWAITING_DATA_REMEDIATION: '仓库业务员',
  AWAITING_RESOURCE_BINDING: '发运协调员',
  AWAITING_RESOURCE_SELECTION: '发运协调员',
  AWAITING_CASE_ACTIVATION: '发运负责人',
  STARTING_LOAD_AGENT: '系统自动处理',
  AWAITING_LOAD_CONSTRAINTS: '装载规划员',
  AWAITING_LOAD_REVIEW: '装载审核员',
  AWAITING_LOAD_RELEASE: '仓库作业负责人',
  AWAITING_LOAD_REPLAN: '装载规划员',
  AWAITING_BOOKING_SUBMIT_APPROVAL: '订舱业务员',
  SUBMITTING_BOOKING: '系统自动处理',
  AWAITING_BOOKING_RESULT: '承运人 / 系统自动跟踪',
  AWAITING_BOOKING_RETRY_APPROVAL: '订舱业务员',
  AWAITING_BOOKING_REPLACEMENT: '订舱业务员',
  AWAITING_COMPLETION_APPROVAL: '发运负责人',
  COMPLETING_CASE: '系统自动处理',
  COMPLETED: '无需处理',
  FAILED: '发运协调员',
  CANCELLED: '发运协调员',
}

const stageNextActionLabels: Record<ShipmentAgentStage, string> = {
  SYNCING_CASE: '等待系统完成同步，无需重复操作',
  AWAITING_SCENARIO_POLICY: '确认本票是否需要智能装载和 LINK 订舱',
  ASSESSING_DATA_READINESS: '等待系统完成资料与库存检查',
  AWAITING_DATA_REMEDIATION: '补充已核实的库存或主数据并提交',
  AWAITING_RESOURCE_BINDING: '准备专业作业并关联到本票案件',
  AWAITING_RESOURCE_SELECTION: '选择本次实际使用的专业作业',
  AWAITING_CASE_ACTIVATION: '核对案件信息并批准进入正式作业',
  STARTING_LOAD_AGENT: '等待系统生成或恢复装载方案',
  AWAITING_LOAD_CONSTRAINTS: '在智能装载中补齐箱规或安全约束',
  AWAITING_LOAD_REVIEW: '打开 3D 装载方案并完成复核',
  AWAITING_LOAD_RELEASE: '发布现场可以执行的正式装载版本',
  AWAITING_LOAD_REPLAN: '修正装载问题并重新规划',
  AWAITING_BOOKING_SUBMIT_APPROVAL: '核对七步填单结果并批准提交',
  SUBMITTING_BOOKING: '等待系统完成一次安全提交',
  AWAITING_BOOKING_RESULT: '等待承运人回复；不要重复提交申请',
  AWAITING_BOOKING_RETRY_APPROVAL: '核对失败原因后决定是否重试',
  AWAITING_BOOKING_REPLACEMENT: '准备新的 LINK 申请并重新关联',
  AWAITING_COMPLETION_APPROVAL: '核对装载和订舱结果后确认结案',
  COMPLETING_CASE: '等待系统写入结案结果',
  COMPLETED: '查看装载、订舱结果或业务记录',
  FAILED: '查看失败原因，修复后再发起处理',
  CANCELLED: '如需继续，基于当前案件重新发起处理',
}

const businessFocus = computed(() => {
  const current = displayRun.value
  if (!current) {
    return {
      phase: '资料与库存核验',
      reason: '本票案件尚未开始自动处理。',
      owner: '发运协调员',
      nextAction: '点击“开始自动处理”，系统会先检查资料和库存。',
      tone: 'ready',
    }
  }

  if (current.status === 'COMPLETED') {
    return {
      phase: '结案',
      reason: '本票所需步骤已经完成，没有待处理的阻塞。',
      owner: '无需处理',
      nextAction: stageNextActionLabels.COMPLETED,
      tone: 'success',
    }
  }

  const isWaiting =
    current.pendingInterrupt !== null ||
    ['WAITING_HUMAN', 'WAITING_RESOURCE', 'WAITING_EXTERNAL'].includes(current.status)
  const blockingCount =
    current.dataReadiness?.blockingIssueCount ??
    current.dataReadiness?.issueCounts?.blocking ??
    current.dataReadiness?.issues.length ??
    0
  const readinessReason =
    current.currentStage === 'AWAITING_DATA_REMEDIATION' && blockingCount > 0
      ? `发现 ${blockingCount} 项库存或主数据问题，系统已安全暂停，避免错误进入装载和订舱。`
      : null
  const focusStage =
    current.status === 'FAILED' || current.status === 'CANCELLED'
      ? [...current.events]
          .reverse()
          .map((event) => event.stage)
          .find((stage) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(stage)) ??
        current.currentStage
      : current.currentStage
  const precisePhase =
    current.status === 'FAILED'
      ? `${stageCopy[focusStage].title}（发生异常）`
      : current.status === 'CANCELLED'
        ? `${stageCopy[focusStage].title}（已停止）`
        : stageCopy[current.currentStage].title
  const failureReason =
    current.status === 'FAILED'
      ? current.failureMessage?.trim() || currentCopy.value.description
      : null
  const currentReason =
    readinessReason ??
    failureReason ??
    (isWaiting || current.status === 'CANCELLED'
      ? currentCopy.value.description
      : '当前没有业务阻塞，系统正在自动处理。')

  return {
    phase: precisePhase,
    reason: currentReason,
    owner: stageOwnerLabels[current.currentStage],
    nextAction: stageNextActionLabels[current.currentStage],
    tone:
      current.status === 'FAILED' || current.status === 'CANCELLED'
        ? 'danger'
        : isWaiting
          ? 'attention'
          : 'running',
  }
})

const pipeline = computed<PipelineStep[]>(() => {
  // 案件没有“活动运行”时，仍用最近一次终态运行还原流程结果。否则一个已经完成的案件
  // 会错误地重新显示“资料核验进行中”，失败运行也会被误画成五步全部完成。
  const pipelineRun = displayRun.value
  if (!pipelineRun) {
    return [
      { code: '01', label: '资料与库存核验', state: 'current' },
      { code: '02', label: '智能装载', state: 'pending' },
      { code: '03', label: 'LINK 订舱', state: 'pending' },
      { code: '04', label: '承运人确认', state: 'pending' },
      { code: '05', label: '结案', state: 'pending' },
    ]
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
    ? businessStageIndex[lastBusinessStage ?? 'SYNCING_CASE']
    : businessStageIndex[pipelineRun.currentStage]

  return businessStageLabels.map((label, index) => ({
    code: String(index + 1).padStart(2, '0'),
    label,
    state: !isBusinessStageRequired(pipelineRun, index)
      ? 'skipped'
      : completed
        ? 'done'
        : stopped && index >= currentIndex
          ? 'stopped'
          : index < currentIndex
            ? 'done'
            : index === currentIndex
              ? 'current'
              : 'pending',
  }))
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

const waitingStatuses = new Set<ShipmentAgentRunStatus>([
  'RUNNING',
  'WAITING_RESOURCE',
  'WAITING_EXTERNAL',
])

const shouldAutoRefreshWaitingState = computed(
  () =>
    Boolean(
      run.value &&
        (waitingStatuses.has(run.value.status) ||
          run.value.wakeReason === 'REPAIR_CONTROL_PROJECTION'),
    ),
)

const showsAutoTrackingCard = computed(
  () =>
    Boolean(run.value) &&
    !run.value?.pendingInterrupt &&
    (Boolean(run.value?.nextWakeAt) ||
      ['RUNNING', 'WAITING_RESOURCE', 'WAITING_EXTERNAL'].includes(
        run.value?.status ?? '',
      )),
)

const lastRun = computed(() => history.value[0] ?? null)

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
    loadError.value = error instanceof Error ? error.message : '读取自动处理状态失败'
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
    actionError.value = error instanceof Error ? error.message : '开始自动处理失败'
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

function formatBusinessDateTime(value: string | null): string {
  if (!value) return '尚未安排'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '尚未安排'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function wakeReasonLabel(value: string | null): string {
  if (!value) return '按当前业务阶段重新检查'
  const labels: Record<string, string> = {
    RECOVER_RUNNING_STEP: '恢复尚未完成的自动步骤',
    POLL_LINK_STATUS: '跟踪 LINK 与承运人结果',
    POLL_LOAD_STATUS: '跟踪智能装载进度',
    POLL_RESOURCE_BINDING: '检查专业作业是否已经绑定',
    RETRY_AFTER_AUTO_WAKE_FAILURE: '等待退避时间后自动重试',
    // 保留早期开发版本的兼容映射，历史快照仍能显示为业务语言。
    LOAD_PLANNING_PROGRESS: '跟踪智能装载进度',
    LINK_BOOKING_RESULT: '跟踪 LINK 与承运人结果',
    AUTOMATIC_RECOVERY: '恢复尚未完成的自动步骤',
    RETRY_AFTER_FAILURE: '等待退避时间后重试',
  }
  return labels[value] ?? '按当前业务阶段重新检查'
}

function notificationOwnerLabel(value: string): string {
  const labels: Record<string, string> = {
    SYSTEM: '系统自动处理',
    SHIPMENT_COORDINATOR: '发运协调员',
    SHIPMENT_OWNER: '发运负责人',
    SHIPMENT_MANAGER: '发运经理',
    WAREHOUSE_OPERATOR: '仓库业务员',
    WAREHOUSE_SUPERVISOR: '仓库主管',
    LOAD_PLANNER: '装载规划员',
    LOAD_REVIEWER: '装载审核员',
    BOOKING_OPERATOR: '订舱业务员',
    PLATFORM_OPERATOR: '平台运营人员',
  }
  return labels[value] ?? value
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

onMounted(() => {
  void loadAgent()
  waitingRefreshTimer = window.setInterval(() => {
    if (
      shouldAutoRefreshWaitingState.value &&
      !document.hidden &&
      !loading.value &&
      !actionBusy.value
    ) {
      void loadAgent()
    }
  }, 5_000)
})

onBeforeUnmount(() => {
  if (waitingRefreshTimer !== undefined) {
    window.clearInterval(waitingRefreshTimer)
  }
})
</script>

<template>
  <section class="shipment-agent-panel">
    <header class="agent-command-header">
      <div class="agent-command-identity">
        <span class="agent-orb"><UiIcon name="server" /></span>
        <div>
          <span>本票自动协同</span>
          <h3>当前处理进度</h3>
          <p>系统按顺序核验资料、推进装载和订舱；遇到缺失或风险动作时，会明确交给对应人员处理。</p>
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
          自动化记录
          <UiIcon name="arrow" />
        </RouterLink>
      </div>
    </header>

    <div v-if="loadError" class="agent-inline-error" role="alert">
      <strong>自动处理状态读取失败</strong>
      <span>{{ loadError }}</span>
      <button type="button" @click="loadAgent">重试</button>
    </div>

    <div v-if="loading && !run && history.length === 0" class="agent-panel-loading">
      <span></span>
      <div>
        <strong>正在读取本票处理状态</strong>
        <p>本次刷新只同步最新结果，不会重复提交任何业务动作。</p>
      </div>
    </div>

    <template v-else>
      <section class="business-focus-card" :data-tone="businessFocus.tone">
        <div class="business-focus-heading">
          <div>
            <span>当前业务判断</span>
            <h4>{{ businessFocus.phase }}</h4>
          </div>
          <span class="business-status">
            {{ displayRun ? statusLabels[displayRun.status] : '等待开始' }}
          </span>
        </div>

        <div class="business-focus-grid">
          <article>
            <span>当前阶段</span>
            <strong>{{ businessFocus.phase }}</strong>
          </article>
          <article>
            <span>当前情况</span>
            <strong>{{ businessFocus.reason }}</strong>
          </article>
          <article>
            <span>当前责任人</span>
            <strong>{{ businessFocus.owner }}</strong>
          </article>
          <article class="next-action">
            <span>下一步</span>
            <strong>{{ businessFocus.nextAction }}</strong>
          </article>
        </div>
      </section>

      <section
        v-if="openNotifications.length"
        class="agent-notification-panel"
        aria-label="当前开放通知"
      >
        <header>
          <div>
            <span>当前开放通知</span>
            <strong>{{ openNotifications.length }} 项业务关注事项</strong>
          </div>
          <small>相同问题会合并更新，不会重复生成通知</small>
        </header>
        <div class="agent-notification-list">
          <article
            v-for="notification in openNotifications"
            :key="notification.id"
            :data-severity="notification.severity"
          >
            <span><UiIcon :name="notification.severity === 'ERROR' ? 'alert' : 'clock'" /></span>
            <div>
              <small>
                {{ notificationOwnerLabel(notification.ownerRole) }}
                · 最近更新 {{ formatBusinessDateTime(notification.lastSeenAt) }}
              </small>
              <strong>{{ notification.title }}</strong>
              <p>{{ notification.message }}</p>
            </div>
            <em v-if="notification.occurrenceCount > 1">
              已合并 {{ notification.occurrenceCount }} 次
            </em>
          </article>
        </div>
      </section>

      <section v-if="!run && (!lastRun || canStart)" class="agent-start-card">
        <span class="agent-start-icon"><UiIcon name="layers" /></span>
        <div>
          <span>{{ lastRun ? '可以重新发起' : '等待开始' }}</span>
          <h4>
            {{ lastRun ? '当前没有正在执行的自动流程' : '开始处理本票发运案件' }}
          </h4>
          <p v-if="lastRun">
            上一次状态为“{{ statusLabels[lastRun.status] }}”。如果业务仍需继续，系统会基于案件当前资料重新检查，
            不会沿用过期快照。
          </p>
          <p v-else>
            系统会先核验运输场景、库存和已关联作业；资料不足时会生成明确待办，
            不会跳过提交审批或结案确认。
          </p>
        </div>
        <button v-if="canStart" type="button" :disabled="actionBusy" @click="startAgent">
          <UiIcon name="plus" />
          {{ actionBusy ? '正在开始…' : '开始自动处理' }}
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
          v-else-if="canReconcile || showsAutoTrackingCard"
          class="agent-wait-card"
          :data-status="run.status"
        >
          <span><UiIcon :name="run.status === 'WAITING_EXTERNAL' ? 'clock' : 'layers'" /></span>
          <div>
            <strong>
              {{
                isAutomaticRecoveryState
                  ? '系统正在自动恢复本票流程'
                  : run.status === 'WAITING_EXTERNAL'
                  ? '系统正在自动跟踪承运人回复'
                  : '系统正在自动跟踪专业作业进度'
              }}
            </strong>
            <p>
              {{
                run.nextWakeAt
                  ? `已安排在 ${formatBusinessDateTime(run.nextWakeAt)} 自动检查；系统会从当前暂停点读取权威状态，已完成的步骤不会重复提交。`
                  : '页面会自动同步最新状态；外部结果或专业作业完成后，系统会从当前暂停点继续，不需要业务人员重复提交。'
              }}
            </p>
            <div v-if="run.nextWakeAt" class="agent-wake-meta">
              <span>{{ wakeReasonLabel(run.wakeReason) }}</span>
              <strong>下次自动检查 {{ formatBusinessDateTime(run.nextWakeAt) }}</strong>
            </div>
          </div>
          <button
            v-if="canReconcile"
            class="fallback-check"
            type="button"
            :disabled="actionBusy"
            @click="reconcile"
          >
            <UiIcon name="refresh" />
            {{ actionBusy ? '检查中…' : '立即检查（故障兜底）' }}
          </button>
        </section>

        <div class="agent-pipeline" aria-label="发运业务进度">
          <div
            v-for="step in pipeline"
            :key="step.code"
            class="agent-pipeline-step"
            :data-state="step.state"
          >
            <span>
              <UiIcon v-if="step.state === 'done'" name="check" />
              <template v-else-if="step.state === 'skipped'">—</template>
              <template v-else>{{ step.code }}</template>
            </span>
            <strong>
              {{ step.label }}
              <small v-if="step.state === 'skipped'">不适用</small>
            </strong>
          </div>
        </div>

        <section class="agent-detail-main">
          <ShipmentAgentWorkstreams :run="run" />
        </section>

        <details class="agent-technical-details">
          <summary>
            <span>
              <UiIcon name="server" />
              自动化技术详情与运行轨迹
            </span>
            <small>默认收起，供开发、审计和故障排查使用</small>
          </summary>
          <div class="technical-fact-grid">
            <div><span>Run ID</span><code>{{ run.runId }}</code></div>
            <div><span>当前节点</span><code>{{ run.currentStage }}</code></div>
            <div><span>工作流版本</span><code>{{ run.workflowVersion }}</code></div>
            <div><span>策略 / 运行版本</span><code>{{ run.policyVersion }} · R{{ run.runVersion }}</code></div>
            <div><span>下次自动检查</span><code>{{ formatBusinessDateTime(run.nextWakeAt) }}</code></div>
            <div><span>唤醒原因</span><code>{{ run.wakeReason || '—' }}</code></div>
            <div v-if="run.childAgent">
              <span>装载子运行</span>
              <code>{{ run.childAgent.runId }}</code>
            </div>
            <div v-if="run.childAgent">
              <span>子运行状态 / 版本</span>
              <code>{{ run.childAgent.status }} · R{{ run.childAgent.runVersion }}</code>
            </div>
          </div>
          <ShipmentAgentTrace :events="run.events" />
        </details>
      </template>

      <div v-else class="agent-pipeline" aria-label="发运业务进度">
        <div
          v-for="step in pipeline"
          :key="step.code"
          class="agent-pipeline-step"
          :data-state="step.state"
        >
          <span>
            <UiIcon v-if="step.state === 'done'" name="check" />
            <template v-else-if="step.state === 'skipped'">—</template>
            <template v-else>{{ step.code }}</template>
          </span>
          <strong>
            {{ step.label }}
            <small v-if="step.state === 'skipped'">不适用</small>
          </strong>
        </div>
      </div>

      <section v-if="!run && lastRun" class="agent-detail-main">
        <ShipmentAgentWorkstreams :run="lastRun" />
      </section>

      <details v-if="!run && lastRun" class="agent-technical-details">
        <summary>
          <span><UiIcon name="clock" />最近一次自动处理记录</span>
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

.business-focus-card {
  overflow: hidden;
  border: 1px solid #d9e5df;
  border-radius: 15px;
  background:
    linear-gradient(120deg, rgba(238, 247, 242, 0.94), rgba(255, 255, 255, 0.98));
}

.business-focus-card[data-tone="attention"] {
  border-color: #e6d1a7;
  background:
    linear-gradient(120deg, rgba(255, 247, 229, 0.94), rgba(255, 255, 255, 0.98));
}

.business-focus-card[data-tone="danger"] {
  border-color: #e8caca;
  background:
    linear-gradient(120deg, rgba(255, 241, 241, 0.94), rgba(255, 255, 255, 0.98));
}

.business-focus-card[data-tone="success"] {
  border-color: #bfdccb;
  background:
    linear-gradient(120deg, rgba(234, 247, 239, 0.94), rgba(255, 255, 255, 0.98));
}

.business-focus-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px 17px 13px;
  border-bottom: 1px solid rgba(216, 226, 221, 0.86);
}

.business-focus-heading > div > span {
  color: #6b8479;
  font-size: 11px;
  font-weight: 780;
  letter-spacing: 0.13em;
}

.business-focus-heading h4 {
  margin: 5px 0 0;
  color: #23352e;
  font-size: 19px;
}

.business-status {
  padding: 6px 10px;
  border-radius: 999px;
  color: #315f4f;
  background: rgba(220, 239, 230, 0.88);
  font-size: 11px;
  font-weight: 750;
  white-space: nowrap;
}

[data-tone="attention"] .business-status {
  color: #805d1f;
  background: #f5e5c4;
}

[data-tone="danger"] .business-status {
  color: #8b4848;
  background: #f6dddd;
}

.business-focus-grid {
  display: grid;
  grid-template-columns: 0.75fr 1.65fr 0.9fr 1.4fr;
}

.business-focus-grid article {
  min-width: 0;
  padding: 15px 17px 17px;
  border-right: 1px solid rgba(222, 230, 226, 0.86);
}

.business-focus-grid article:last-child {
  border-right: 0;
}

.business-focus-grid span,
.business-focus-grid strong {
  display: block;
}

.business-focus-grid span {
  color: #87938e;
  font-size: 11px;
}

.business-focus-grid strong {
  margin-top: 7px;
  color: #34433d;
  font-size: 13px;
  line-height: 1.55;
}

.business-focus-grid .next-action {
  background: rgba(255, 255, 255, 0.52);
}

.business-focus-grid .next-action strong {
  color: #285d4b;
}

.agent-notification-panel {
  overflow: hidden;
  border: 1px solid #e1e7e4;
  border-radius: 14px;
  background: #fff;
}

.agent-notification-panel > header {
  display: flex;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 14px;
  border-bottom: 1px solid #edf0ef;
  background: #fafbfb;
}

.agent-notification-panel > header span,
.agent-notification-panel > header strong {
  display: block;
}

.agent-notification-panel > header span {
  color: #7b8983;
  font-size: 11px;
}

.agent-notification-panel > header strong {
  margin-top: 3px;
  color: #34413c;
  font-size: 13px;
}

.agent-notification-panel > header small {
  color: #929b97;
  font-size: 11px;
}

.agent-notification-list {
  display: grid;
}

.agent-notification-list article {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 11px;
  align-items: start;
  padding: 13px 14px;
  border-bottom: 1px solid #edf0ef;
}

.agent-notification-list article:last-child {
  border-bottom: 0;
}

.agent-notification-list article > span {
  display: grid;
  width: 31px;
  height: 31px;
  place-items: center;
  border-radius: 9px;
  color: #4e7193;
  background: #e9f0f7;
}

.agent-notification-list article[data-severity="WARNING"] > span {
  color: #88661f;
  background: #f7ecd0;
}

.agent-notification-list article[data-severity="ERROR"] > span {
  color: #985050;
  background: #f8e8e8;
}

.agent-notification-list article > span :deep(.ui-icon) {
  width: 15px;
}

.agent-notification-list small,
.agent-notification-list strong,
.agent-notification-list p {
  display: block;
  margin: 0;
}

.agent-notification-list small {
  color: #929b97;
  font-size: 11px;
}

.agent-notification-list strong {
  margin-top: 4px;
  color: #35423d;
  font-size: 13px;
}

.agent-notification-list p {
  margin-top: 4px;
  color: #727e79;
  font-size: 12px;
  line-height: 1.55;
}

.agent-notification-list em {
  align-self: center;
  padding: 4px 7px;
  border-radius: 999px;
  color: #757f7b;
  background: #f0f2f1;
  font-size: 10px;
  font-style: normal;
  white-space: nowrap;
}

.agent-pipeline {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
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

.agent-pipeline-step strong small {
  display: block;
  margin-top: 2px;
  font-size: 9px;
  font-weight: 650;
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

.agent-pipeline-step[data-state="skipped"] {
  color: #98a19d;
  background:
    repeating-linear-gradient(
      -45deg,
      #f7f9f8,
      #f7f9f8 7px,
      #f2f5f3 7px,
      #f2f5f3 14px
    );
}

.agent-pipeline-step[data-state="skipped"] > span {
  border-style: dashed;
  color: #9ba5a1;
  background: rgba(255, 255, 255, 0.72);
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

.agent-wake-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 8px;
}

.agent-wake-meta span,
.agent-wake-meta strong {
  font-size: 11px;
}

.agent-wake-meta span {
  color: #71807a;
}

.agent-wake-meta strong {
  color: #3f6656;
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

.agent-wait-card > button.fallback-check {
  border-color: #cbd8d2;
  color: #52665e;
  background: #fff;
}

.agent-wait-card > button.fallback-check:hover:not(:disabled) {
  border-color: #9fb8ad;
  color: #2f5e4e;
  background: #f7faf8;
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

.agent-detail-main {
  min-width: 0;
  padding: 16px;
  border: 1px solid #e4e9e7;
  border-radius: 14px;
  background: #fff;
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

  .business-focus-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .business-focus-grid article {
    border-bottom: 1px solid rgba(222, 230, 226, 0.86);
  }

  .business-focus-grid article:nth-child(2) {
    border-right: 0;
  }

  .business-focus-grid article:nth-child(n + 3) {
    border-bottom: 0;
  }

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

  .business-focus-grid {
    grid-template-columns: 1fr;
  }

  .business-focus-grid article,
  .business-focus-grid article:nth-child(n + 3) {
    border-right: 0;
    border-bottom: 1px solid rgba(222, 230, 226, 0.86);
  }

  .business-focus-grid article:last-child {
    border-bottom: 0;
  }

  .agent-notification-panel > header {
    align-items: flex-start;
    flex-direction: column;
  }

  .agent-notification-list article {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .agent-notification-list em {
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
