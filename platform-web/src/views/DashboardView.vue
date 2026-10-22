<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  getLinkBookingApplications,
  getLoadPlanningAgentRunHistory,
  getLoadPlanningTasks,
  getShipmentAgentRuns,
  getShipmentCases,
  type LinkBookingApplication,
  type LoadPlanningAgentRun,
  type LoadPlanningTaskSummary,
  type ShipmentAgentRun,
  type ShipmentAgentStage,
  type ShipmentCase,
  type ShipmentTransportScenario,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

type DataSourceKey = 'shipment' | 'shipmentAgent' | 'loadPlanning' | 'linkApplications'
type DataSourceState = 'idle' | 'ready' | 'error'
type WorkItemTone = 'critical' | 'warning' | 'info'
type ProgressItemKind = 'automatic' | 'external'

interface AgentTaskSnapshot {
  task: LoadPlanningTaskSummary
  latestRun: LoadPlanningAgentRun | null
  state: 'ready' | 'error'
}

interface WorkItem {
  id: string
  eyebrow: string
  title: string
  detail: string
  action: string
  route: string
  updatedAt: string
  tone: WorkItemTone
}

interface ProgressItem {
  id: string
  title: string
  detail: string
  route: string
  updatedAt: string
  kind: ProgressItemKind
}

const router = useRouter()

const loading = ref(true)
const failures = ref<string[]>([])
const lastRefreshedAt = ref<Date | null>(null)
const showAllWorkItems = ref(false)
const shipmentCases = ref<ShipmentCase[]>([])
const shipmentAgentRuns = ref<ShipmentAgentRun[]>([])
const linkApplications = ref<LinkBookingApplication[]>([])
const agentTaskSnapshots = ref<AgentTaskSnapshot[]>([])
const sourceStates = ref<Record<DataSourceKey, DataSourceState>>({
  shipment: 'idle',
  shipmentAgent: 'idle',
  loadPlanning: 'idle',
  linkApplications: 'idle',
})

// 首页必须覆盖全部装载任务，否则第 9 条以后的人工待办会被误报为“不存在”。
// 这里分批读取运行历史，既保留完整性，也避免一次向 BFF 发出过多并发请求。
const AGENT_HISTORY_BATCH_SIZE = 6

const scenarioLabels: Record<ShipmentTransportScenario, string> = {
  UNDECIDED: '运输方式待确认',
  SEA_FCL: '海运整柜',
  SEA_LCL: '海运拼箱',
  AIR: '空运',
  EXPRESS: '国际快递',
  LOAD_PLANNING_ONLY: '仅装载规划',
  BOOKING_ONLY: '仅订舱',
}

const stageLabels: Record<ShipmentAgentStage, string> = {
  SYNCING_CASE: '同步案件资料',
  AWAITING_SCENARIO_POLICY: '确认运输范围',
  ASSESSING_DATA_READINESS: '核验资料与库存',
  AWAITING_DATA_REMEDIATION: '补充资料与库存',
  AWAITING_RESOURCE_BINDING: '准备装载与订舱工作',
  AWAITING_RESOURCE_SELECTION: '选择装载或订舱任务',
  AWAITING_CASE_ACTIVATION: '确认启动案件',
  STARTING_LOAD_AGENT: '生成装载方案',
  AWAITING_LOAD_CONSTRAINTS: '补充装载约束',
  AWAITING_LOAD_REVIEW: '复核装载方案',
  AWAITING_LOAD_RELEASE: '发布正式装载方案',
  AWAITING_LOAD_REPLAN: '调整装载方案',
  DISPATCHING_RPA_JOB: '派发自动填单任务',
  AWAITING_RPA_RESULT: '等待自动填单结果',
  AWAITING_RPA_ATTENTION: '核对自动填单异常',
  AWAITING_BOOKING_SUBMIT_APPROVAL: '确认提交订舱',
  SUBMITTING_BOOKING: '提交订舱申请',
  AWAITING_BOOKING_RESULT: '等待承运人回复',
  AWAITING_BOOKING_RETRY_APPROVAL: '确认重新提交订舱',
  AWAITING_BOOKING_REPLACEMENT: '更换订舱申请',
  AWAITING_COMPLETION_APPROVAL: '确认案件结案',
  COMPLETING_CASE: '完成案件',
  COMPLETED: '已完成',
  FAILED: '流程异常',
  CANCELLED: '已取消',
}

const openCases = computed(() =>
  sortByUpdatedAt(
    shipmentCases.value.filter((shipmentCase) =>
      ['DRAFT', 'ACTIVE'].includes(shipmentCase.status),
    ),
  ),
)

/**
 * 一票案件可能有多次历史运行，首页只读取最近一次。
 *
 * 这样同一票货不会因为历史失败记录、重新执行记录而被重复算成多条待办。
 */
const latestRunByCase = computed(() => {
  const result = new Map<string, ShipmentAgentRun>()
  for (const run of sortByUpdatedAt(shipmentAgentRuns.value)) {
    if (!result.has(run.caseNumber)) {
      result.set(run.caseNumber, run)
    }
  }
  return result
})

const latestShipmentRuns = computed(() => [...latestRunByCase.value.values()])

const attentionShipmentRuns = computed(() =>
  latestShipmentRuns.value.filter((run) =>
    ['WAITING_HUMAN', 'WAITING_RESOURCE', 'FAILED'].includes(run.status),
  ),
)

const automaticShipmentRuns = computed(() =>
  sortByUpdatedAt(latestShipmentRuns.value.filter((run) => run.status === 'RUNNING')),
)

const externalShipmentRuns = computed(() =>
  sortByUpdatedAt(latestShipmentRuns.value.filter((run) => run.status === 'WAITING_EXTERNAL')),
)

const linkedLoadTaskNumbers = computed(
  () =>
    new Set(
      latestShipmentRuns.value
        .map((run) => run.selectedLoadTaskNumber)
        .filter((taskNumber): taskNumber is string => Boolean(taskNumber)),
    ),
)

const linkedBookingNumbers = computed(
  () =>
    new Set(
      latestShipmentRuns.value
        .map((run) => run.selectedBookingApplicationNumber)
        .filter((applicationNumber): applicationNumber is string => Boolean(applicationNumber)),
    ),
)

const agentAttentionSnapshots = computed(() =>
  agentTaskSnapshots.value.filter(({ task, latestRun }) => {
    if (linkedLoadTaskNumbers.value.has(task.taskNumber)) return false
    return latestRun
      ? ['AWAITING_INPUT', 'READY_FOR_REVIEW', 'FAILED', 'REJECTED'].includes(latestRun.status)
      : false
  }),
)

const agentUnavailableCount = computed(
  () => agentTaskSnapshots.value.filter((snapshot) => snapshot.state === 'error').length,
)

const workItems = computed<WorkItem[]>(() => {
  const items: WorkItem[] = []
  const casesManagedByActiveRun = new Set(
    latestShipmentRuns.value
      .filter((run) => !['COMPLETED', 'CANCELLED'].includes(run.status))
      .map((run) => run.caseNumber),
  )

  for (const shipmentCase of openCases.value) {
    // 正在自动推进、等待外部或已经产生明确人工动作的案件，都由最近一次运行统一表达。
    // 不再额外根据“草稿/未关联”生成第二条首页待办，避免同一票货重复计数。
    if (casesManagedByActiveRun.has(shipmentCase.caseNumber)) continue

    if (shipmentCase.resourceBindings.length === 0) {
      items.push({
        id: `case-unbound-${shipmentCase.caseNumber}`,
        eyebrow: '流程准备 · 待确认业务范围',
        title: shipmentCase.caseName,
        detail: `${shipmentCase.caseNumber} 还没有关联装载或订舱工作，请确认本票需要执行的业务环节。`,
        action: '确认后续流程',
        route: shipmentCasePath(shipmentCase.caseNumber),
        updatedAt: shipmentCase.updatedAt,
        tone: 'warning',
      })
    } else if (shipmentCase.status === 'DRAFT') {
      items.push({
        id: `case-draft-${shipmentCase.caseNumber}`,
        eyebrow: '流程准备 · 待启动',
        title: shipmentCase.caseName,
        detail: `${shipmentCase.caseNumber} 已准备好相关业务任务，等待确认并启动。`,
        action: '检查并启动',
        route: shipmentCasePath(shipmentCase.caseNumber),
        updatedAt: shipmentCase.updatedAt,
        tone: 'info',
      })
    }
  }

  for (const run of attentionShipmentRuns.value) {
    if (run.status === 'WAITING_HUMAN') {
      items.push({
        id: `shipment-human-${run.runId}`,
        eyebrow: `需要我处理 · ${stageLabel(run.currentStage)}`,
        title: run.pendingInterrupt?.title ?? run.caseName,
        detail:
          businessText(run.pendingInterrupt?.description) ??
          `${run.caseNumber} 已暂停，等待业务人员确认后继续。`,
        action: '立即处理',
        route: shipmentCasePath(run.caseNumber),
        updatedAt: run.updatedAt,
        tone: 'warning',
      })
    } else if (run.status === 'WAITING_RESOURCE') {
      items.push({
        id: `shipment-resource-${run.runId}`,
        eyebrow: `专业作业 · ${stageLabel(run.currentStage)}`,
        title: run.caseName || run.caseNumber,
        detail: `${run.caseNumber} 正在等待装载、自动填单或订舱工作完成，进入案件即可查看具体要求。`,
        action: '继续办理',
        route: shipmentCasePath(run.caseNumber),
        updatedAt: run.updatedAt,
        tone: 'warning',
      })
    } else if (run.status === 'FAILED') {
      items.push({
        id: `shipment-failed-${run.runId}`,
        eyebrow: '流程异常 · 需要处理',
        title: run.caseName || run.caseNumber,
        detail:
          businessText(run.failureMessage) ??
          `${run.caseNumber} 自动处理未能继续，请检查原因后恢复。`,
        action: '查看并恢复',
        route: shipmentCasePath(run.caseNumber),
        updatedAt: run.updatedAt,
        tone: 'critical',
      })
    }
  }

  for (const snapshot of agentAttentionSnapshots.value) {
    const run = snapshot.latestRun
    if (!run) continue

    if (run.status === 'AWAITING_INPUT') {
      items.push({
        id: `load-input-${run.runId}`,
        eyebrow: '智能装载 · 待补充条件',
        title: snapshot.task.taskName,
        detail: `还有 ${run.questions.length} 项装载条件需要确认，补充后将自动继续计算。`,
        action: '补充装载条件',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    } else if (run.status === 'READY_FOR_REVIEW') {
      items.push({
        id: `load-review-${run.runId}`,
        eyebrow: '智能装载 · 待复核方案',
        title: snapshot.task.taskName,
        detail: `已生成 ${run.plans.length} 套候选方案，等待业务人员确认正式方案。`,
        action: '复核装载方案',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    } else if (run.status === 'FAILED') {
      items.push({
        id: `load-failed-${run.runId}`,
        eyebrow: '智能装载 · 计算异常',
        title: snapshot.task.taskName,
        detail:
          businessText(run.failureMessage) ??
          '装载方案计算未完成，请检查任务数据后重新执行。',
        action: '处理装载异常',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'critical',
      })
    } else if (run.status === 'REJECTED') {
      items.push({
        id: `load-rejected-${run.runId}`,
        eyebrow: '智能装载 · 待重新规划',
        title: snapshot.task.taskName,
        detail: '上一轮方案未通过，需要调整条件并重新生成装载方案。',
        action: '重新规划',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    }
  }

  for (const application of linkApplications.value) {
    if (linkedBookingNumbers.value.has(application.applicationNumber)) continue

    if (application.status === 'REJECTED') {
      items.push({
        id: `booking-rejected-${application.applicationNumber}`,
        eyebrow: '订舱申请 · 承运人未接受',
        title: application.applicationNumber,
        detail: `${application.originPort} → ${application.destinationPort}，请检查反馈并重新处理。`,
        action: '处理订舱申请',
        route: linkApplicationPath(application.applicationNumber, 'detail'),
        updatedAt: application.updatedAt,
        tone: 'critical',
      })
    } else if (application.status === 'DRAFT') {
      items.push({
        id: `booking-draft-${application.applicationNumber}`,
        eyebrow: '订舱申请 · 待完成',
        title: application.applicationNumber,
        detail: `${application.originPort} → ${application.destinationPort}，申请尚未填写完成。`,
        action: '继续填写',
        route: linkApplicationPath(application.applicationNumber, 'wizard'),
        updatedAt: application.updatedAt,
        tone: 'info',
      })
    }
  }

  const tonePriority: Record<WorkItemTone, number> = {
    critical: 0,
    warning: 1,
    info: 2,
  }

  return items.sort(
    (left, right) =>
      tonePriority[left.tone] - tonePriority[right.tone] ||
      dateTimestamp(right.updatedAt) - dateTimestamp(left.updatedAt),
  )
})

const displayedWorkItems = computed(() =>
  showAllWorkItems.value ? workItems.value : workItems.value.slice(0, 6),
)

const criticalWorkCount = computed(
  () => workItems.value.filter((item) => item.tone === 'critical').length,
)

const hasAttentionCoverageGap = computed(
  () =>
    sourceStates.value.shipment === 'error' ||
    sourceStates.value.shipmentAgent === 'error' ||
    sourceStates.value.loadPlanning === 'error' ||
    sourceStates.value.linkApplications === 'error' ||
    agentUnavailableCount.value > 0,
)

const attentionCountLabel = computed(() => {
  if (loading.value) return '—'
  if (!hasAttentionCoverageGap.value) return String(workItems.value.length)
  return workItems.value.length > 0 ? `${workItems.value.length}+` : '—'
})

const todaySummary = computed(() => {
  if (loading.value) return '正在汇总案件进展与今天需要处理的工作。'
  if (workItems.value.length === 0 && hasAttentionCoverageGap.value) {
    return '部分业务数据尚未同步，当前不能确认是否还有待处理事项。'
  }
  if (workItems.value.length === 0) {
    return '当前没有需要你立即处理的事项，系统会继续推进已启动的案件。'
  }
  if (criticalWorkCount.value > 0) {
    return `今天有 ${workItems.value.length} 项工作需要处理，其中 ${criticalWorkCount.value} 项异常应优先查看。`
  }
  return `今天有 ${workItems.value.length} 项工作需要处理，按下方顺序完成即可。`
})

const progressItems = computed<ProgressItem[]>(() => {
  const items: ProgressItem[] = automaticShipmentRuns.value.map((run) => ({
    id: `automatic-${run.runId}`,
    title: run.caseName || run.caseNumber,
    detail: `${stageLabel(run.currentStage)}，暂时不需要人工操作。`,
    route: shipmentCasePath(run.caseNumber),
    updatedAt: run.updatedAt,
    kind: 'automatic',
  }))

  for (const run of externalShipmentRuns.value) {
    items.push({
      id: `external-${run.runId}`,
      title: run.caseName || run.caseNumber,
      detail:
        run.currentStage === 'AWAITING_RPA_RESULT'
          ? 'LINK 自动填单已派发，正在等待机器人保存草稿，请勿手工重复执行同一票。'
          : '订舱申请已发出，正在等待承运人回复，请勿重复提交。',
      route: shipmentCasePath(run.caseNumber),
      updatedAt: run.updatedAt,
      kind: 'external',
    })
  }

  for (const application of linkApplications.value) {
    if (
      application.status !== 'SUBMITTED' ||
      linkedBookingNumbers.value.has(application.applicationNumber)
    ) {
      continue
    }
    items.push({
      id: `external-booking-${application.applicationNumber}`,
      title: application.applicationNumber,
      detail: `${application.originPort} → ${application.destinationPort}，正在等待承运人确认。`,
      route: linkApplicationPath(application.applicationNumber, 'detail'),
      updatedAt: application.updatedAt,
      kind: 'external',
    })
  }

  return items.sort(
    (left, right) => dateTimestamp(right.updatedAt) - dateTimestamp(left.updatedAt),
  )
})

const externalWaitCount = computed(
  () => progressItems.value.filter((item) => item.kind === 'external').length,
)

const focusCases = computed(() => openCases.value.slice(0, 6))

async function loadAgentSnapshots(tasks: LoadPlanningTaskSummary[]): Promise<void> {
  const sortedTasks = sortByUpdatedAt(tasks)
  const results: PromiseSettledResult<LoadPlanningAgentRun[]>[] = []

  for (let index = 0; index < sortedTasks.length; index += AGENT_HISTORY_BATCH_SIZE) {
    const batch = sortedTasks.slice(index, index + AGENT_HISTORY_BATCH_SIZE)
    results.push(
      ...(await Promise.allSettled(
        batch.map((task) => getLoadPlanningAgentRunHistory(task.taskNumber)),
      )),
    )
  }

  agentTaskSnapshots.value = sortedTasks.map((task, index) => {
    const result = results[index]
    if (result?.status === 'fulfilled') {
      return {
        task,
        latestRun: result.value[0] ?? null,
        state: 'ready',
      }
    }
    return { task, latestRun: null, state: 'error' }
  })

  if (results.some((result) => result.status === 'rejected')) {
    failures.value.push('部分装载任务状态暂时无法同步')
  }
}

/**
 * 各业务来源独立加载。某一项暂时不可用时，页面仍展示其余已确认的工作，
 * 但不会把“没有读到数据”误写成“没有待办”。
 */
async function loadDashboard(): Promise<void> {
  loading.value = true
  failures.value = []

  const [shipmentResult, shipmentAgentResult, taskResult, linkApplicationResult] =
    await Promise.allSettled([
      getShipmentCases(),
      getShipmentAgentRuns(),
      getLoadPlanningTasks(),
      getLinkBookingApplications(),
    ])

  if (shipmentResult.status === 'fulfilled') {
    shipmentCases.value = shipmentResult.value
    sourceStates.value.shipment = 'ready'
  } else {
    shipmentCases.value = []
    sourceStates.value.shipment = 'error'
    failures.value.push('发运案件暂时无法同步')
  }

  if (shipmentAgentResult.status === 'fulfilled') {
    shipmentAgentRuns.value = shipmentAgentResult.value
    sourceStates.value.shipmentAgent = 'ready'
  } else {
    shipmentAgentRuns.value = []
    sourceStates.value.shipmentAgent = 'error'
    failures.value.push('案件处理进展暂时无法同步')
  }

  if (taskResult.status === 'fulfilled') {
    sourceStates.value.loadPlanning = 'ready'
    await loadAgentSnapshots(taskResult.value)
  } else {
    agentTaskSnapshots.value = []
    sourceStates.value.loadPlanning = 'error'
    failures.value.push('装载任务暂时无法同步')
  }

  if (linkApplicationResult.status === 'fulfilled') {
    linkApplications.value = linkApplicationResult.value
    sourceStates.value.linkApplications = 'ready'
  } else {
    linkApplications.value = []
    sourceStates.value.linkApplications = 'error'
    failures.value.push('订舱申请暂时无法同步')
  }

  lastRefreshedAt.value = new Date()
  loading.value = false
}

function sortByUpdatedAt<T extends { updatedAt: string }>(values: T[]): T[] {
  return [...values].sort(
    (left, right) => dateTimestamp(right.updatedAt) - dateTimestamp(left.updatedAt),
  )
}

function dateTimestamp(value: string): number {
  const timestamp = new Date(value).getTime()
  return Number.isNaN(timestamp) ? 0 : timestamp
}

function shipmentCasePath(caseNumber: string): string {
  return `/tradeops/shipment-cases/${encodeURIComponent(caseNumber)}`
}

function loadPlanningTaskPath(taskNumber: string): string {
  return `/supply-chain/load-planning/tasks?taskNumber=${encodeURIComponent(taskNumber)}&view=planning`
}

function linkApplicationPath(
  applicationNumber: string,
  view: 'detail' | 'wizard',
): string {
  return `/link-booking/applications?applicationNumber=${encodeURIComponent(applicationNumber)}&view=${view}`
}

function latestAgentActivityAt(snapshot: AgentTaskSnapshot): string {
  const latestEvent = snapshot.latestRun?.events.reduce<string | null>((latest, event) => {
    if (!latest || dateTimestamp(event.createdAt) > dateTimestamp(latest)) {
      return event.createdAt
    }
    return latest
  }, null)
  return latestEvent ?? snapshot.task.updatedAt
}

function stageLabel(stage: ShipmentAgentStage): string {
  return stageLabels[stage] ?? '处理中'
}

/**
 * 后端审计信息保留稳定技术枚举，普通业务首页只做最小翻译。
 * 原始内容仍可在案件技术详情中查看，这里不让 DRAFT、Agent、Trace 等词干扰办理。
 */
function businessText(value: string | null | undefined): string | null {
  if (!value) return null
  return value
    .replace(/Shipment Agent/g, '发运协同')
    .replace(/总 Agent/g, '系统')
    .replace(/装载 Agent/g, '智能装载')
    .replace(/Agent/g, '系统')
    .replace(/DRAFT/g, '草稿状态')
    .replace(/Trace/g, '处理记录')
}

function caseProgressLabel(shipmentCase: ShipmentCase): string {
  const run = latestRunByCase.value.get(shipmentCase.caseNumber)
  if (run?.status === 'WAITING_HUMAN') return '等待业务处理'
  if (run?.status === 'WAITING_RESOURCE') return stageLabel(run.currentStage)
  if (run?.status === 'WAITING_EXTERNAL') return '等待外部系统回复'
  if (run?.status === 'RUNNING') return stageLabel(run.currentStage)
  if (run?.status === 'FAILED') return '流程异常'
  if (shipmentCase.resourceBindings.length === 0) return '待确认后续流程'
  if (shipmentCase.status === 'DRAFT') return '待启动'
  return '业务进行中'
}

function caseProgressTone(shipmentCase: ShipmentCase): string {
  const run = latestRunByCase.value.get(shipmentCase.caseNumber)
  if (run?.status === 'FAILED') return 'critical'
  if (run?.status === 'WAITING_HUMAN' || run?.status === 'WAITING_RESOURCE') return 'warning'
  if (run?.status === 'RUNNING') return 'automatic'
  if (run?.status === 'WAITING_EXTERNAL') return 'external'
  if (shipmentCase.resourceBindings.length === 0 || shipmentCase.status === 'DRAFT') {
    return 'warning'
  }
  return 'neutral'
}

function caseStageDescription(shipmentCase: ShipmentCase): string {
  const run = latestRunByCase.value.get(shipmentCase.caseNumber)
  if (run?.status === 'WAITING_HUMAN') {
    return run.pendingInterrupt?.title ?? stageLabel(run.currentStage)
  }
  if (run) return stageLabel(run.currentStage)
  return shipmentCase.resourceBindings.length
    ? `已关联 ${shipmentCase.resourceBindings.length} 项后续工作`
    : '尚未安排装载或订舱工作'
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
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

onMounted(loadDashboard)
</script>

<template>
  <section class="work-dashboard">
    <header class="work-hero">
      <div class="hero-copy">
        <span class="hero-kicker">今日工作台</span>
        <h2>我的工作</h2>
        <p>{{ todaySummary }}</p>
        <div class="hero-actions">
          <button
            v-if="workItems[0]"
            class="primary-action"
            type="button"
            @click="router.push(workItems[0].route)"
          >
            <UiIcon name="check" />
            处理首项待办
          </button>
          <button
            class="secondary-action"
            type="button"
            @click="router.push('/tradeops/shipment-cases')"
          >
            <UiIcon name="truck" />
            查看全部发运案件
          </button>
        </div>
      </div>

      <div class="today-overview" aria-label="今日工作概览">
        <div class="overview-item attention">
          <span class="overview-icon"><UiIcon name="check" /></span>
          <span>
            <small>待我处理</small>
            <strong>{{ attentionCountLabel }}</strong>
            <p>{{ criticalWorkCount ? `${criticalWorkCount} 项异常优先` : '按优先级依次办理' }}</p>
          </span>
        </div>
        <div class="overview-item automatic">
          <span class="overview-icon"><UiIcon name="refresh" /></span>
          <span>
            <small>系统处理中</small>
            <strong>{{ sourceStates.shipmentAgent === 'ready' ? automaticShipmentRuns.length : '—' }}</strong>
            <p>无需人工重复操作</p>
          </span>
        </div>
        <div class="overview-item external">
          <span class="overview-icon"><UiIcon name="clock" /></span>
          <span>
            <small>等待外部回复</small>
            <strong>
              {{
                sourceStates.shipmentAgent === 'ready' &&
                sourceStates.linkApplications === 'ready'
                  ? externalWaitCount
                  : '—'
              }}
            </strong>
            <p>已提交，继续跟踪中</p>
          </span>
        </div>
        <div class="refresh-row">
          <span>最近同步 {{ formatRefreshTime(lastRefreshedAt) }}</span>
          <button type="button" :disabled="loading" @click="loadDashboard">
            <UiIcon name="refresh" />
            {{ loading ? '同步中' : '刷新' }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="failures.length" class="degraded-banner" role="alert">
      <UiIcon name="alert" />
      <div>
        <strong>部分业务数据暂时未同步</strong>
        <p>{{ failures.join('；') }}。页面已保留其他可用内容，未知状态不会被算作“没有待办”。</p>
      </div>
      <button type="button" :disabled="loading" @click="loadDashboard">重新同步</button>
    </div>

    <div class="primary-grid">
      <section class="dashboard-panel work-queue-panel">
        <div class="panel-heading">
          <div>
            <span class="section-kicker">按优先级排列</span>
            <h3>待我处理</h3>
            <p>每项工作都给出当前原因和下一步，进入后即可继续办理。</p>
          </div>
          <span class="panel-count" :class="{ critical: criticalWorkCount > 0 }">
            {{ attentionCountLabel }}
          </span>
        </div>

        <div v-if="loading && !displayedWorkItems.length" class="panel-loading">
          <span></span>
          正在整理今天的工作…
        </div>
        <div v-else-if="displayedWorkItems.length" class="work-list">
          <button
            v-for="item in displayedWorkItems"
            :key="item.id"
            class="work-item"
            :data-tone="item.tone"
            type="button"
            @click="router.push(item.route)"
          >
            <span class="work-marker"></span>
            <div class="work-main">
              <span>{{ item.eyebrow }}</span>
              <strong>{{ item.title }}</strong>
              <p>{{ item.detail }}</p>
            </div>
            <div class="work-meta">
              <time>{{ formatDateTime(item.updatedAt) }}</time>
              <span>{{ item.action }} <UiIcon name="arrow" /></span>
            </div>
          </button>
          <button
            v-if="workItems.length > 6"
            class="queue-more"
            type="button"
            @click="showAllWorkItems = !showAllWorkItems"
          >
            {{
              showAllWorkItems
                ? '收起'
                : `查看其余 ${workItems.length - displayedWorkItems.length} 项`
            }}
          </button>
        </div>
        <div v-else-if="hasAttentionCoverageGap" class="panel-empty uncertain">
          <span><UiIcon name="alert" /></span>
          <strong>暂时无法确认全部待办</strong>
          <p>部分业务数据尚未同步，请刷新后再确认今天的工作。</p>
        </div>
        <div v-else class="panel-empty">
          <span><UiIcon name="check" /></span>
          <strong>当前没有需要你处理的事项</strong>
          <p>已启动的案件会由系统继续推进，出现需要决策的事项时会回到这里。</p>
        </div>
      </section>

      <aside class="dashboard-panel progress-panel">
        <div class="panel-heading">
          <div>
            <span class="section-kicker">无需重复操作</span>
            <h3>系统正在推进</h3>
            <p>这里仅展示自动处理和等待外部回复的案件。</p>
          </div>
        </div>

        <div class="progress-summary">
          <div>
            <span class="summary-dot automatic"></span>
            <span>
              <small>自动处理中</small>
              <strong>
                {{ sourceStates.shipmentAgent === 'ready' ? automaticShipmentRuns.length : '—' }}
              </strong>
            </span>
          </div>
          <div>
            <span class="summary-dot external"></span>
            <span>
              <small>等待外部回复</small>
              <strong>
                {{
                  sourceStates.shipmentAgent === 'ready' &&
                  sourceStates.linkApplications === 'ready'
                    ? externalWaitCount
                    : '—'
                }}
              </strong>
            </span>
          </div>
        </div>

        <div v-if="progressItems.length" class="progress-list">
          <button
            v-for="item in progressItems.slice(0, 5)"
            :key="item.id"
            type="button"
            @click="router.push(item.route)"
          >
            <span class="progress-rail" :data-kind="item.kind"></span>
            <span class="progress-copy">
              <small>{{ item.kind === 'automatic' ? '自动处理中' : '等待外部回复' }}</small>
              <strong>{{ item.title }}</strong>
              <p>{{ item.detail }}</p>
            </span>
            <time>{{ formatDateTime(item.updatedAt) }}</time>
          </button>
        </div>
        <div v-else-if="loading" class="progress-empty">
          正在同步处理进展…
        </div>
        <div v-else-if="hasAttentionCoverageGap" class="progress-empty uncertain">
          部分处理进展暂时无法同步
        </div>
        <div v-else class="progress-empty">
          当前没有自动处理或等待外部回复的案件
        </div>

        <div class="progress-note">
          <UiIcon name="shield" />
          <p>等待外部回复时请勿重复提交；系统收到结果后会自动更新案件并生成下一步。</p>
        </div>
      </aside>
    </div>

    <section class="dashboard-panel case-panel">
      <div class="panel-heading">
        <div>
          <span class="section-kicker">案件进展</span>
          <h3>风险与最近案件</h3>
          <p>从一票案件查看资料核验、装载、订舱到承运人确认的完整进展。</p>
        </div>
        <button class="heading-link" type="button" @click="router.push('/tradeops/shipment-cases')">
          查看全部案件 <UiIcon name="arrow" />
        </button>
      </div>

      <div v-if="focusCases.length" class="case-table">
        <div class="case-table-head">
          <span>案件</span>
          <span>当前进展</span>
          <span>运输方式</span>
          <span>更新时间</span>
        </div>
        <button
          v-for="shipmentCase in focusCases"
          :key="shipmentCase.id"
          class="case-row"
          type="button"
          @click="router.push(shipmentCasePath(shipmentCase.caseNumber))"
        >
          <span class="case-identity">
            <strong>{{ shipmentCase.caseName }}</strong>
            <small>{{ shipmentCase.caseNumber }}</small>
          </span>
          <span class="case-progress">
            <i :data-tone="caseProgressTone(shipmentCase)">
              {{ caseProgressLabel(shipmentCase) }}
            </i>
            <small>{{ caseStageDescription(shipmentCase) }}</small>
          </span>
          <span class="scenario-label">{{ scenarioLabels[shipmentCase.transportScenario] }}</span>
          <span class="case-updated">
            {{ formatDateTime(shipmentCase.updatedAt) }}
            <UiIcon name="arrow" />
          </span>
        </button>
      </div>
      <div v-else-if="sourceStates.shipment === 'error'" class="panel-empty compact uncertain">
        <span><UiIcon name="alert" /></span>
        <strong>案件暂时无法同步</strong>
        <p>当前不能确认是否还有未结案件，请稍后重新同步。</p>
      </div>
      <div v-else class="panel-empty compact">
        <span><UiIcon name="truck" /></span>
        <strong>暂无未结发运案件</strong>
        <p>可以从发运案件页面创建第一票业务。</p>
      </div>
    </section>
  </section>
</template>

<style scoped>
.work-dashboard {
  display: grid;
  gap: 18px;
  color: #17211d;
}

button {
  font: inherit;
}

.work-hero {
  position: relative;
  display: grid;
  min-height: 250px;
  grid-template-columns: minmax(0, 1.35fr) minmax(390px, 0.8fr);
  gap: 36px;
  overflow: hidden;
  padding: 34px 38px;
  border: 1px solid rgba(36, 79, 66, 0.52);
  border-radius: 20px;
  color: #f7fbf9;
  background:
    radial-gradient(circle at 90% 5%, rgba(111, 192, 156, 0.18), transparent 34%),
    linear-gradient(135deg, #172b25 0%, #1d4034 58%, #142b25 100%);
  box-shadow: 0 18px 45px rgba(28, 57, 48, 0.12);
}

.work-hero::after {
  position: absolute;
  top: -110px;
  right: -80px;
  width: 290px;
  height: 290px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 50%;
  box-shadow:
    0 0 0 34px rgba(255, 255, 255, 0.02),
    0 0 0 78px rgba(255, 255, 255, 0.014);
  content: "";
  pointer-events: none;
}

.hero-copy,
.today-overview {
  position: relative;
  z-index: 1;
}

.hero-copy {
  align-self: center;
}

.hero-kicker,
.section-kicker {
  display: block;
  color: #729986;
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
}

.hero-kicker {
  color: #9ed7bc;
}

.hero-copy h2 {
  margin: 10px 0 12px;
  font-size: clamp(28px, 3vw, 39px);
  font-weight: 690;
  letter-spacing: -0.045em;
}

.hero-copy > p {
  max-width: 650px;
  margin: 0;
  color: rgba(240, 249, 245, 0.76);
  font-size: 14px;
  line-height: 1.75;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 26px;
}

.hero-actions button {
  display: inline-flex;
  min-height: 42px;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.hero-actions .ui-icon {
  width: 16px;
  height: 16px;
}

.primary-action {
  border: 1px solid #b8e4cf;
  color: #173b2f;
  background: #b8e4cf;
}

.primary-action:hover {
  background: #cef0df;
}

.secondary-action {
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #f3faf6;
  background: rgba(255, 255, 255, 0.07);
}

.secondary-action:hover {
  background: rgba(255, 255, 255, 0.12);
}

.today-overview {
  display: grid;
  align-self: center;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.065);
  backdrop-filter: blur(8px);
}

.overview-item {
  display: grid;
  min-width: 0;
  min-height: 128px;
  align-content: center;
  gap: 10px;
  padding: 17px 15px;
  border-right: 1px solid rgba(255, 255, 255, 0.1);
}

.overview-item:last-of-type {
  border-right: 0;
}

.overview-icon {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 9px;
  color: #f3c98e;
  background: rgba(235, 176, 91, 0.14);
}

.overview-item.automatic .overview-icon {
  color: #9fd2e2;
  background: rgba(99, 169, 192, 0.14);
}

.overview-item.external .overview-icon {
  color: #bdc8ef;
  background: rgba(128, 143, 202, 0.16);
}

.overview-icon .ui-icon {
  width: 15px;
  height: 15px;
}

.overview-item small,
.overview-item strong,
.overview-item p {
  display: block;
}

.overview-item small {
  color: rgba(241, 249, 245, 0.65);
  font-size: 10px;
}

.overview-item strong {
  margin-top: 2px;
  color: #ffffff;
  font-size: 28px;
  font-weight: 650;
  letter-spacing: -0.04em;
}

.overview-item p {
  overflow: hidden;
  margin: 3px 0 0;
  color: rgba(241, 249, 245, 0.57);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.refresh-row {
  display: flex;
  grid-column: 1 / -1;
  min-height: 42px;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  color: rgba(241, 249, 245, 0.58);
  font-size: 9px;
}

.refresh-row button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  color: #dceee5;
  background: transparent;
  cursor: pointer;
  font-size: 9px;
  font-weight: 700;
}

.refresh-row button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.refresh-row .ui-icon {
  width: 12px;
  height: 12px;
}

.degraded-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 15px;
  border: 1px solid #ecd8b3;
  border-radius: 12px;
  color: #765b27;
  background: #fffbf1;
}

.degraded-banner > .ui-icon {
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
}

.degraded-banner div {
  flex: 1;
}

.degraded-banner strong {
  display: block;
  font-size: 12px;
}

.degraded-banner p {
  margin: 4px 0 0;
  color: #8d7443;
  font-size: 10px;
  line-height: 1.5;
}

.degraded-banner button {
  border: 0;
  color: #765b27;
  background: transparent;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
}

.primary-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(330px, 0.75fr);
  gap: 14px;
}

.dashboard-panel {
  overflow: hidden;
  border: 1px solid #e2e7e4;
  border-radius: 16px;
  background: #ffffff;
}

.panel-heading {
  display: flex;
  min-height: 86px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 19px 21px;
  border-bottom: 1px solid #edf0ee;
}

.panel-heading h3 {
  margin: 5px 0 0;
  color: #1e2924;
  font-size: 16px;
  font-weight: 680;
}

.panel-heading p {
  margin: 5px 0 0;
  color: #8b948f;
  font-size: 10px;
  line-height: 1.5;
}

.panel-count {
  display: grid;
  min-width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 10px;
  color: #8a622e;
  background: #fcf1df;
  font-size: 12px;
  font-weight: 750;
}

.panel-count.critical {
  color: #a25149;
  background: #faeae8;
}

.panel-loading {
  display: flex;
  min-height: 300px;
  align-items: center;
  justify-content: center;
  gap: 9px;
  color: #8b938f;
  font-size: 11px;
}

.panel-loading span {
  width: 11px;
  height: 11px;
  border: 2px solid #c8d7d0;
  border-top-color: #4c806d;
  border-radius: 50%;
  animation: dashboard-spin 700ms linear infinite;
}

@keyframes dashboard-spin {
  to {
    transform: rotate(360deg);
  }
}

.work-list {
  padding: 4px 20px;
}

.work-item {
  display: grid;
  width: 100%;
  min-height: 88px;
  grid-template-columns: 8px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  padding: 12px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: inherit;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.work-item:hover .work-main strong,
.work-item:hover .work-meta > span {
  color: #2f6b57;
}

.work-marker {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #7897a5;
  box-shadow: 0 0 0 4px rgba(120, 151, 165, 0.12);
}

.work-item[data-tone="warning"] .work-marker {
  background: #d29a4d;
  box-shadow: 0 0 0 4px rgba(210, 154, 77, 0.12);
}

.work-item[data-tone="critical"] .work-marker {
  background: #c9685d;
  box-shadow: 0 0 0 4px rgba(201, 104, 93, 0.12);
}

.work-main {
  min-width: 0;
}

.work-main > span {
  display: block;
  color: #808a85;
  font-size: 9px;
}

.work-main strong {
  display: block;
  overflow: hidden;
  margin-top: 4px;
  color: #24302b;
  font-size: 12px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-main p {
  overflow: hidden;
  margin: 5px 0 0;
  color: #727d77;
  font-size: 10px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-meta {
  display: grid;
  min-width: 112px;
  justify-items: end;
  gap: 10px;
}

.work-meta time {
  color: #9ca49f;
  font-size: 9px;
}

.work-meta > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #4f7866;
  font-size: 9px;
  font-weight: 750;
}

.work-meta .ui-icon,
.heading-link .ui-icon {
  width: 12px;
  height: 12px;
}

.queue-more {
  width: 100%;
  padding: 12px;
  border: 0;
  border-top: 1px solid #eff1f0;
  color: #587566;
  background: #fbfcfb;
  cursor: pointer;
  font-size: 9px;
}

.panel-empty {
  display: grid;
  min-height: 300px;
  place-content: center;
  justify-items: center;
  padding: 30px;
  text-align: center;
}

.panel-empty.compact {
  min-height: 190px;
}

.panel-empty > span {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 12px;
  color: #51806d;
  background: #edf5f1;
}

.panel-empty.uncertain > span {
  color: #8a672b;
  background: #fbf2df;
}

.panel-empty strong {
  margin-top: 12px;
  color: #36423d;
  font-size: 12px;
}

.panel-empty p {
  max-width: 340px;
  margin: 6px 0 0;
  color: #8e9792;
  font-size: 10px;
  line-height: 1.6;
}

.progress-panel {
  background:
    linear-gradient(180deg, rgba(245, 248, 247, 0.72), transparent 130px),
    #ffffff;
}

.progress-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 14px 17px 8px;
}

.progress-summary > div {
  display: flex;
  min-width: 0;
  min-height: 67px;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: 1px solid #e8ecea;
  border-radius: 12px;
  background: #fbfcfb;
}

.summary-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 auto;
  border-radius: 50%;
}

.summary-dot.automatic {
  background: #5b91a6;
  box-shadow: 0 0 0 4px rgba(91, 145, 166, 0.12);
}

.summary-dot.external {
  background: #7e84b3;
  box-shadow: 0 0 0 4px rgba(126, 132, 179, 0.12);
}

.progress-summary small,
.progress-summary strong {
  display: block;
}

.progress-summary small {
  color: #7e8883;
  font-size: 9px;
}

.progress-summary strong {
  margin-top: 3px;
  color: #26322c;
  font-size: 18px;
  font-weight: 680;
}

.progress-list {
  padding: 2px 17px 6px;
}

.progress-list button {
  display: grid;
  width: 100%;
  min-height: 69px;
  grid-template-columns: 3px minmax(0, 1fr) auto;
  gap: 11px;
  align-items: center;
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: inherit;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.progress-list button:hover .progress-copy strong {
  color: #326c58;
}

.progress-rail {
  width: 3px;
  height: 34px;
  border-radius: 4px;
  background: #6594a6;
}

.progress-rail[data-kind="external"] {
  background: #7e84b3;
}

.progress-copy {
  min-width: 0;
}

.progress-copy small,
.progress-copy strong,
.progress-copy p {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-copy small {
  color: #89928d;
  font-size: 8px;
}

.progress-copy strong {
  margin-top: 3px;
  color: #2a3631;
  font-size: 11px;
  font-weight: 680;
}

.progress-copy p {
  margin: 4px 0 0;
  color: #818a85;
  font-size: 9px;
}

.progress-list time {
  color: #a0a7a3;
  font-size: 8px;
  white-space: nowrap;
}

.progress-empty {
  min-height: 148px;
  padding: 52px 24px;
  color: #919994;
  text-align: center;
  font-size: 10px;
}

.progress-empty.uncertain {
  color: #8a6a38;
}

.progress-note {
  display: flex;
  gap: 9px;
  margin: 8px 17px 16px;
  padding: 11px 12px;
  border: 1px solid #e4ebe7;
  border-radius: 11px;
  color: #65736c;
  background: #f7f9f8;
}

.progress-note .ui-icon {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  color: #517763;
}

.progress-note p {
  margin: 0;
  font-size: 9px;
  line-height: 1.6;
}

.heading-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  color: #507461;
  background: transparent;
  cursor: pointer;
  font-size: 9px;
  font-weight: 750;
  white-space: nowrap;
}

.case-table {
  padding: 0 20px 8px;
}

.case-table-head,
.case-row {
  display: grid;
  grid-template-columns:
    minmax(210px, 1.3fr)
    minmax(210px, 1.15fr)
    minmax(110px, 0.65fr)
    105px;
  gap: 16px;
  align-items: center;
}

.case-table-head {
  min-height: 42px;
  border-bottom: 1px solid #edf0ee;
  color: #929b96;
  font-size: 9px;
}

.case-row {
  width: 100%;
  min-height: 72px;
  padding: 8px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: #59645f;
  text-align: left;
  background: transparent;
  cursor: pointer;
  font-size: 10px;
}

.case-row:last-child {
  border-bottom: 0;
}

.case-row:hover .case-identity strong {
  color: #2f6b57;
}

.case-identity,
.case-progress {
  min-width: 0;
}

.case-identity strong,
.case-identity small,
.case-progress small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-identity strong {
  color: #29352f;
  font-size: 11px;
  font-weight: 680;
}

.case-identity small {
  margin-top: 5px;
  color: #939b97;
  font-size: 9px;
}

.case-progress i {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 999px;
  color: #526f60;
  background: #edf4f0;
  font-size: 8px;
  font-style: normal;
  font-weight: 750;
}

.case-progress i[data-tone="warning"] {
  color: #89682f;
  background: #faf1df;
}

.case-progress i[data-tone="critical"] {
  color: #9b514b;
  background: #fae9e7;
}

.case-progress i[data-tone="automatic"] {
  color: #496f7e;
  background: #e9f2f5;
}

.case-progress i[data-tone="external"] {
  color: #616795;
  background: #eef0f8;
}

.case-progress small {
  margin-top: 5px;
  color: #909894;
  font-size: 8px;
}

.scenario-label {
  color: #69746e;
}

.case-updated {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  color: #8d9691;
  font-size: 9px;
}

.case-updated .ui-icon {
  width: 12px;
  height: 12px;
  color: #afb6b2;
}

@media (max-width: 1100px) {
  .work-hero {
    grid-template-columns: 1fr;
  }

  .today-overview {
    width: 100%;
  }

  .primary-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .work-hero {
    padding: 28px 24px;
  }

  .today-overview {
    grid-template-columns: 1fr;
  }

  .overview-item {
    min-height: 88px;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    border-right: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  }

  .overview-item:last-of-type {
    border-bottom: 0;
  }

  .refresh-row {
    grid-column: auto;
  }

  .case-table-head {
    display: none;
  }

  .case-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
  }

  .scenario-label,
  .case-updated {
    display: none;
  }
}

@media (max-width: 560px) {
  .hero-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .hero-actions button {
    width: 100%;
    justify-content: center;
  }

  .degraded-banner {
    align-items: flex-start;
  }

  .degraded-banner button {
    display: none;
  }

  .panel-heading {
    align-items: flex-start;
    padding: 17px;
  }

  .work-list,
  .case-table {
    padding-right: 16px;
    padding-left: 16px;
  }

  .work-item {
    grid-template-columns: 8px minmax(0, 1fr);
  }

  .work-main p {
    white-space: normal;
  }

  .work-meta {
    display: none;
  }

  .progress-summary {
    grid-template-columns: 1fr;
  }

  .progress-list button {
    grid-template-columns: 3px minmax(0, 1fr);
  }

  .progress-list time {
    display: none;
  }

  .case-row {
    grid-template-columns: 1fr;
  }
}
</style>
