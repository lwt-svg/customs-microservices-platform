<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  getLinkBookingApplications,
  getLinkBookingHealth,
  getLoadPlanningAgentRunHistory,
  getLoadPlanningTasks,
  getPlatformHealth,
  getShipmentAgentRuns,
  getShipmentCases,
  type HealthData,
  type LinkBookingApplication,
  type LinkHealthData,
  type LoadPlanningAgentRun,
  type LoadPlanningTaskSummary,
  type ShipmentAgentRun,
  type ShipmentCase,
  type ShipmentCaseStatus,
  type ShipmentTransportScenario,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

type DataSourceKey =
  | 'platform'
  | 'shipment'
  | 'shipmentAgent'
  | 'loadPlanning'
  | 'linkApplications'
  | 'linkHealth'
type DataSourceState = 'idle' | 'ready' | 'error'
type WorkItemTone = 'critical' | 'warning' | 'info'

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

const router = useRouter()

const loading = ref(true)
const failures = ref<string[]>([])
const lastRefreshedAt = ref<Date | null>(null)
const showAllWorkItems = ref(false)
const platformHealth = ref<HealthData | null>(null)
const linkHealth = ref<LinkHealthData | null>(null)
const shipmentCases = ref<ShipmentCase[]>([])
const shipmentAgentRuns = ref<ShipmentAgentRun[]>([])
const loadPlanningTasks = ref<LoadPlanningTaskSummary[]>([])
const linkApplications = ref<LinkBookingApplication[]>([])
const agentTaskSnapshots = ref<AgentTaskSnapshot[]>([])
const sourceStates = ref<Record<DataSourceKey, DataSourceState>>({
  platform: 'idle',
  shipment: 'idle',
  shipmentAgent: 'idle',
  loadPlanning: 'idle',
  linkApplications: 'idle',
  linkHealth: 'idle',
})

const MAX_AGENT_TASKS = 8

const shipmentStatusLabels: Record<ShipmentCaseStatus, string> = {
  DRAFT: '草稿',
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

const scenarioLabels: Record<ShipmentTransportScenario, string> = {
  UNDECIDED: '运输方式待确认',
  SEA_FCL: '海运整柜',
  SEA_LCL: '海运拼箱',
  AIR: '空运',
  EXPRESS: '国际快递',
  LOAD_PLANNING_ONLY: '仅装载规划',
  BOOKING_ONLY: '仅订舱',
}

const platformOnline = computed(() => platformHealth.value?.status === 'UP')
const linkOnline = computed(() => ['UP', 'ok', 'OK'].includes(linkHealth.value?.status ?? ''))

const openCases = computed(() =>
  sortByUpdatedAt(
    shipmentCases.value.filter((shipmentCase) =>
      ['DRAFT', 'ACTIVE'].includes(shipmentCase.status),
    ),
  ),
)

const activeCaseCount = computed(
  () => shipmentCases.value.filter((shipmentCase) => shipmentCase.status === 'ACTIVE').length,
)

const unboundCases = computed(() =>
  openCases.value.filter((shipmentCase) => shipmentCase.resourceBindings.length === 0),
)

const shipmentAgentAttentionRuns = computed(() =>
  shipmentAgentRuns.value.filter((run) =>
    ['WAITING_HUMAN', 'WAITING_RESOURCE', 'FAILED'].includes(run.status),
  ),
)

const shipmentAgentHumanCount = computed(
  () => shipmentAgentRuns.value.filter((run) => run.status === 'WAITING_HUMAN').length,
)

const shipmentAgentExternalCount = computed(
  () => shipmentAgentRuns.value.filter((run) => run.status === 'WAITING_EXTERNAL').length,
)

const linkDraftCount = computed(
  () => linkApplications.value.filter((application) => application.status === 'DRAFT').length,
)

const linkRejectedCount = computed(
  () => linkApplications.value.filter((application) => application.status === 'REJECTED').length,
)

const linkWaitingCount = computed(
  () => linkApplications.value.filter((application) => application.status === 'SUBMITTED').length,
)

const agentAttentionSnapshots = computed(() =>
  agentTaskSnapshots.value.filter(({ latestRun }) =>
    latestRun
      ? ['AWAITING_INPUT', 'READY_FOR_REVIEW', 'FAILED', 'REJECTED'].includes(latestRun.status)
      : false,
  ),
)

const agentUnavailableCount = computed(
  () => agentTaskSnapshots.value.filter((snapshot) => snapshot.state === 'error').length,
)

const visibleAttentionCount = computed(
  () =>
    unboundCases.value.length +
    shipmentAgentAttentionRuns.value.length +
    linkDraftCount.value +
    linkRejectedCount.value +
    agentAttentionSnapshots.value.length,
)

/**
 * “没有读到待办”和“确认没有待办”是两件不同的事。
 *
 * 只要任一待办来源读取失败，或最近任务中有 Agent 历史未同步，驾驶舱就把总数标为
 * “至少 N 项”，并禁止使用绿色的“暂无待办”结论。
 */
const hasAttentionCoverageGap = computed(
  () =>
    sourceStates.value.shipment === 'error' ||
    sourceStates.value.shipmentAgent === 'error' ||
    sourceStates.value.loadPlanning === 'error' ||
    sourceStates.value.linkApplications === 'error' ||
    agentUnavailableCount.value > 0,
)

const visibleAttentionLabel = computed(() =>
  hasAttentionCoverageGap.value
    ? visibleAttentionCount.value > 0
      ? `${visibleAttentionCount.value}+`
      : '—'
    : String(visibleAttentionCount.value),
)

const focusCases = computed(() => openCases.value.slice(0, 5))

const workItems = computed<WorkItem[]>(() => {
  const items: WorkItem[] = []

  for (const shipmentCase of openCases.value) {
    if (shipmentCase.resourceBindings.length === 0) {
      items.push({
        id: `case-unbound-${shipmentCase.caseNumber}`,
        eyebrow: '发运案件 · 资源未关联',
        title: shipmentCase.caseName,
        detail: `${shipmentCase.caseNumber} 尚未绑定装载任务或 LINK 申请。`,
        action: '打开案件工作台',
        route: shipmentCasePath(shipmentCase.caseNumber),
        updatedAt: shipmentCase.updatedAt,
        tone: 'warning',
      })
    } else if (shipmentCase.status === 'DRAFT') {
      items.push({
        id: `case-draft-${shipmentCase.caseNumber}`,
        eyebrow: '发运案件 · 待启动',
        title: shipmentCase.caseName,
        detail: `${shipmentCase.caseNumber} 已有关联资源，案件仍处于草稿状态。`,
        action: '检查并启动',
        route: shipmentCasePath(shipmentCase.caseNumber),
        updatedAt: shipmentCase.updatedAt,
        tone: 'info',
      })
    }
  }

  for (const run of shipmentAgentAttentionRuns.value) {
    if (run.status === 'WAITING_HUMAN') {
      items.push({
        id: `shipment-agent-human-${run.runId}`,
        eyebrow: 'Shipment Agent · 等待人工决策',
        title: run.pendingInterrupt?.title ?? run.caseNumber,
        detail:
          run.pendingInterrupt?.description ??
          `${run.caseNumber} 已在 ${run.currentStage} 节点安全暂停。`,
        action: '处理 Agent 待办',
        route: shipmentCasePath(run.caseNumber),
        updatedAt: run.updatedAt,
        tone: 'warning',
      })
    } else if (run.status === 'WAITING_RESOURCE') {
      items.push({
        id: `shipment-agent-resource-${run.runId}`,
        eyebrow: 'Shipment Agent · 等待专业作业',
        title: run.caseNumber,
        detail: `${run.currentStage} 需要先在装载或订舱工作台完成业务处理。`,
        action: '打开案件并处理',
        route: shipmentCasePath(run.caseNumber),
        updatedAt: run.updatedAt,
        tone: 'info',
      })
    } else if (run.status === 'FAILED') {
      items.push({
        id: `shipment-agent-failed-${run.runId}`,
        eyebrow: 'Shipment Agent · 编排失败',
        title: run.caseNumber,
        detail: run.failureMessage || `${run.currentStage} 节点失败，请查看运行 Trace。`,
        action: '查看失败轨迹',
        route: '/tradeops/agents',
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
        id: `agent-input-${run.runId}`,
        eyebrow: '装载 Agent · 等待人工输入',
        title: snapshot.task.taskName,
        detail: `${run.questions.length} 项约束需要确认，Agent 已安全暂停。`,
        action: '补充约束',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    } else if (run.status === 'READY_FOR_REVIEW') {
      items.push({
        id: `agent-review-${run.runId}`,
        eyebrow: '装载 Agent · 待方案审批',
        title: snapshot.task.taskName,
        detail: `${run.plans.length} 套候选方案等待业务复核。`,
        action: '复核方案',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    } else if (run.status === 'FAILED') {
      items.push({
        id: `agent-failed-${run.runId}`,
        eyebrow: '装载 Agent · 运行异常',
        title: snapshot.task.taskName,
        detail: run.failureMessage || '最近一次规划运行失败，需要检查原因。',
        action: '处理异常',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'critical',
      })
    } else if (run.status === 'REJECTED') {
      items.push({
        id: `agent-rejected-${run.runId}`,
        eyebrow: '装载 Agent · 待重新规划',
        title: snapshot.task.taskName,
        detail: '上一轮候选方案已被驳回，需要发起新的规划运行。',
        action: '重新规划',
        route: loadPlanningTaskPath(snapshot.task.taskNumber),
        updatedAt: latestAgentActivityAt(snapshot),
        tone: 'warning',
      })
    }
  }

  for (const application of linkApplications.value) {
    if (application.status === 'REJECTED') {
      items.push({
        id: `link-rejected-${application.applicationNumber}`,
        eyebrow: 'LINK 订舱 · 承运人拒绝',
        title: application.applicationNumber,
        detail: `${application.originPort} → ${application.destinationPort}，需要业务检查后重新处理。`,
        action: '查看申请',
        route: linkApplicationPath(application.applicationNumber, 'detail'),
        updatedAt: application.updatedAt,
        tone: 'critical',
      })
    } else if (application.status === 'DRAFT') {
      items.push({
        id: `link-draft-${application.applicationNumber}`,
        eyebrow: 'LINK 订舱 · 待完成填单',
        title: application.applicationNumber,
        detail: `${application.originPort} → ${application.destinationPort}，草稿尚未提交。`,
        action: '继续七步填单',
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
  showAllWorkItems.value ? workItems.value : workItems.value.slice(0, 7),
)

/**
 * 当前后端还没有驾驶舱聚合接口。
 *
 * 为避免任务数量增长后形成无界 N+1 请求，驾驶舱只读取最近八个装载任务的运行历史；
 * 页面会明确展示这个统计口径，不把样本误报成全部 Agent 运行。
 */
async function loadAgentSnapshots(tasks: LoadPlanningTaskSummary[]): Promise<void> {
  const recentTasks = sortByUpdatedAt(tasks).slice(0, MAX_AGENT_TASKS)
  const results = await Promise.allSettled(
    recentTasks.map((task) => getLoadPlanningAgentRunHistory(task.taskNumber)),
  )

  agentTaskSnapshots.value = recentTasks.map((task, index) => {
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
    failures.value.push('部分装载 Agent 运行状态未能同步')
  }
}

/**
 * 各业务域独立降级：某个 Python 服务不可用时，其余案件和待办仍可继续展示。
 *
 * 浏览器只调用 Java BFF 暴露的接口；这里不会拼接或直连 Python 服务地址。
 */
async function loadDashboard(): Promise<void> {
  loading.value = true
  failures.value = []

  const [
    platformResult,
    shipmentResult,
    shipmentAgentResult,
    taskResult,
    linkApplicationResult,
    linkHealthResult,
  ] =
    await Promise.allSettled([
      getPlatformHealth(),
      getShipmentCases(),
      getShipmentAgentRuns(),
      getLoadPlanningTasks(),
      getLinkBookingApplications(),
      getLinkBookingHealth(),
    ])

  if (platformResult.status === 'fulfilled') {
    platformHealth.value = platformResult.value
    sourceStates.value.platform = 'ready'
  } else {
    platformHealth.value = null
    sourceStates.value.platform = 'error'
    failures.value.push('Java BFF 健康状态读取失败')
  }

  if (shipmentResult.status === 'fulfilled') {
    shipmentCases.value = shipmentResult.value
    sourceStates.value.shipment = 'ready'
  } else {
    shipmentCases.value = []
    sourceStates.value.shipment = 'error'
    failures.value.push('发运案件数据读取失败')
  }

  if (shipmentAgentResult.status === 'fulfilled') {
    shipmentAgentRuns.value = shipmentAgentResult.value
    sourceStates.value.shipmentAgent = 'ready'
  } else {
    shipmentAgentRuns.value = []
    sourceStates.value.shipmentAgent = 'error'
    failures.value.push('Shipment Agent 运行数据读取失败')
  }

  if (taskResult.status === 'fulfilled') {
    loadPlanningTasks.value = taskResult.value
    sourceStates.value.loadPlanning = 'ready'
    await loadAgentSnapshots(taskResult.value)
  } else {
    loadPlanningTasks.value = []
    sourceStates.value.loadPlanning = 'error'
    agentTaskSnapshots.value = []
    failures.value.push('装载任务数据读取失败')
  }

  if (linkApplicationResult.status === 'fulfilled') {
    linkApplications.value = linkApplicationResult.value
    sourceStates.value.linkApplications = 'ready'
  } else {
    linkApplications.value = []
    sourceStates.value.linkApplications = 'error'
    failures.value.push('LINK 订舱申请读取失败')
  }

  if (linkHealthResult.status === 'fulfilled') {
    linkHealth.value = linkHealthResult.value
    sourceStates.value.linkHealth = 'ready'
  } else {
    linkHealth.value = null
    sourceStates.value.linkHealth = 'error'
    failures.value.push('LINK 服务健康状态读取失败')
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

function resourceCount(shipmentCase: ShipmentCase, type: 'LOAD' | 'LINK'): number {
  const resourceType =
    type === 'LOAD' ? 'LOAD_PLANNING_TASK' : 'LINK_BOOKING_APPLICATION'
  return shipmentCase.resourceBindings.filter((binding) => binding.resourceType === resourceType)
    .length
}

function agentStatusLabel(snapshot: AgentTaskSnapshot): string {
  if (snapshot.state === 'error') return '状态未同步'
  const status = snapshot.latestRun?.status
  if (!status) return '暂无 Agent 运行'
  const labels: Record<LoadPlanningAgentRun['status'], string> = {
    AWAITING_INPUT: '等待补充约束',
    READY_FOR_REVIEW: '等待方案审批',
    APPROVED: '方案已批准',
    REJECTED: '待重新规划',
    FAILED: '运行异常',
  }
  return labels[status]
}

function agentStatusTone(snapshot: AgentTaskSnapshot): string {
  if (snapshot.state === 'error') return 'unknown'
  const status = snapshot.latestRun?.status
  if (!status) return 'idle'
  if (status === 'FAILED' || status === 'REJECTED') return 'danger'
  if (status === 'AWAITING_INPUT' || status === 'READY_FOR_REVIEW') return 'waiting'
  return 'success'
}

function serviceStateLabel(state: DataSourceState): string {
  if (state === 'ready') return '数据可读取'
  if (state === 'error') return '读取失败'
  return '尚未同步'
}

onMounted(loadDashboard)
</script>

<template>
  <section class="operations-dashboard">
    <header class="dashboard-hero">
      <div class="hero-copy">
        <span class="hero-kicker">TRADEOPS CONTROL TOWER</span>
        <h2>跨境发运运营驾驶舱</h2>
        <p>
          从一票案件进入装载规划与 LINK 订舱，优先处理等待人工判断、资源未关联和运行异常的工作。
        </p>
        <div class="hero-actions">
          <button class="hero-primary" type="button" @click="router.push('/tradeops/shipment-cases')">
            <UiIcon name="truck" />
            进入发运案件
          </button>
          <button
            class="hero-secondary"
            type="button"
            @click="router.push('/tradeops/agents')"
          >
            <UiIcon name="server" />
            打开 Agent 运行中心
          </button>
        </div>
      </div>

      <div class="hero-status">
        <div
          class="attention-orb"
          :class="{
            clear: visibleAttentionCount === 0 && !hasAttentionCoverageGap,
            partial: hasAttentionCoverageGap,
          }"
        >
          <span>当前可见待办</span>
          <strong>{{ loading ? '—' : visibleAttentionLabel }}</strong>
          <small>
            {{
              !loading && hasAttentionCoverageGap
                ? '部分来源未知'
                : visibleAttentionCount === 0 && !loading
                  ? '暂无需人工介入事项'
                  : '项需要关注'
            }}
          </small>
        </div>
        <div class="sync-state">
          <span :class="{ online: platformOnline }"></span>
          <div>
            <small>最近同步尝试</small>
            <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
          </div>
          <button type="button" :disabled="loading" title="刷新驾驶舱" @click="loadDashboard">
            <UiIcon name="refresh" />
          </button>
        </div>
      </div>
    </header>

    <div v-if="failures.length" class="degraded-banner" role="alert">
      <UiIcon name="alert" />
      <div>
        <strong>部分数据暂时不可用，驾驶舱已降级展示</strong>
        <p>{{ failures.join('；') }}。其余成功来源仍可继续使用。</p>
      </div>
      <button type="button" :disabled="loading" @click="loadDashboard">重新同步</button>
    </div>

    <section class="metric-grid" aria-label="运营关键指标">
      <button class="operation-metric active" type="button" @click="router.push('/tradeops/shipment-cases')">
        <span class="metric-icon"><UiIcon name="truck" /></span>
        <div class="metric-copy">
          <small>进行中案件</small>
          <strong>{{ loading || sourceStates.shipment !== 'ready' ? '—' : activeCaseCount }}</strong>
          <p>
            {{
              sourceStates.shipment === 'ready'
                ? `共 ${openCases.length} 个未结案件`
                : '案件来源尚未确认'
            }}
          </p>
        </div>
        <UiIcon class="metric-arrow" name="arrow" />
      </button>

      <button class="operation-metric warning" type="button" @click="router.push('/tradeops/shipment-cases')">
        <span class="metric-icon"><UiIcon name="alert" /></span>
        <div class="metric-copy">
          <small>资源待关联</small>
          <strong>{{ loading || sourceStates.shipment !== 'ready' ? '—' : unboundCases.length }}</strong>
          <p>{{ sourceStates.shipment === 'ready' ? '案件尚未绑定专业作业' : '案件来源尚未确认' }}</p>
        </div>
        <UiIcon class="metric-arrow" name="arrow" />
      </button>

      <button
        class="operation-metric orchestrator"
        type="button"
        @click="router.push('/tradeops/agents')"
      >
        <span class="metric-icon"><UiIcon name="server" /></span>
        <div class="metric-copy">
          <small>总 Agent 待处理</small>
          <strong>
            {{
              loading || sourceStates.shipmentAgent !== 'ready'
                ? '—'
                : shipmentAgentAttentionRuns.length
            }}
          </strong>
          <p>
            {{
              sourceStates.shipmentAgent === 'ready'
                ? `人工 ${shipmentAgentHumanCount} · 外部等待 ${shipmentAgentExternalCount}`
                : '总编排运行来源尚未确认'
            }}
          </p>
        </div>
        <UiIcon class="metric-arrow" name="arrow" />
      </button>

      <button
        class="operation-metric agent"
        type="button"
        @click="router.push('/supply-chain/load-planning/tasks')"
      >
        <span class="metric-icon"><UiIcon name="layers" /></span>
        <div class="metric-copy">
          <small>装载 Agent 待处理</small>
          <strong>
            {{
              loading || sourceStates.loadPlanning !== 'ready'
                ? '—'
                : agentUnavailableCount
                  ? `${agentAttentionSnapshots.length}+`
                  : agentAttentionSnapshots.length
            }}
          </strong>
          <p>
            {{
              sourceStates.loadPlanning === 'ready'
                ? `最近 ${agentTaskSnapshots.length} / ${loadPlanningTasks.length} 个任务已同步运行状态`
                : '装载任务来源尚未确认'
            }}
          </p>
        </div>
        <UiIcon class="metric-arrow" name="arrow" />
      </button>

      <button
        class="operation-metric booking"
        type="button"
        @click="router.push('/link-booking/applications')"
      >
        <span class="metric-icon"><UiIcon name="database" /></span>
        <div class="metric-copy">
          <small>LINK 待处理</small>
          <strong>
            {{
              loading || sourceStates.linkApplications !== 'ready'
                ? '—'
                : linkDraftCount + linkRejectedCount
            }}
          </strong>
          <p>
            {{
              sourceStates.linkApplications === 'ready'
                ? `草稿 ${linkDraftCount} · 拒绝 ${linkRejectedCount} · 在途 ${linkWaitingCount}`
                : '订舱申请来源尚未确认'
            }}
          </p>
        </div>
        <UiIcon class="metric-arrow" name="arrow" />
      </button>
    </section>

    <div class="primary-grid">
      <section class="dashboard-panel work-queue-panel">
        <div class="panel-heading">
          <div>
            <span class="section-kicker">HUMAN-IN-THE-LOOP</span>
            <h3>我的运营待办</h3>
            <p>只列出已有业务状态能够明确判断的人工动作和异常。</p>
          </div>
          <span class="panel-count">{{ workItems.length }}</span>
        </div>

        <div v-if="loading && !displayedWorkItems.length" class="panel-loading">
          <span></span>
          正在汇总跨域作业状态…
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
            v-if="workItems.length > 7"
            class="queue-more"
            type="button"
            @click="showAllWorkItems = !showAllWorkItems"
          >
            {{
              showAllWorkItems
                ? '收起待办'
                : `展开其余 ${workItems.length - displayedWorkItems.length} 项待办`
            }}
          </button>
        </div>
        <div v-else-if="hasAttentionCoverageGap" class="panel-empty uncertain">
          <span><UiIcon name="alert" /></span>
          <strong>部分来源未完成同步</strong>
          <p>当前没有读取到明确待办，但不能据此判断全部业务都已正常，请先恢复上方提示的数据通道。</p>
        </div>
        <div v-else class="panel-empty">
          <span><UiIcon name="check" /></span>
          <strong>当前没有明确待办</strong>
          <p>系统不会把未知状态伪装成“正常”；请同时留意下方的数据通道状态。</p>
        </div>
      </section>

      <aside class="professional-panel">
        <div class="panel-heading compact">
          <div>
            <span class="section-kicker">PROFESSIONAL WORKBENCH</span>
            <h3>专业作业入口</h3>
            <p>从案件总览进入具体业务执行。</p>
          </div>
        </div>

        <div class="professional-list">
          <button type="button" @click="router.push('/tradeops/agents')">
            <span class="professional-icon agent"><UiIcon name="server" /></span>
            <span>
              <small>AGENT CONTROL PLANE</small>
              <strong>Agent 运行中心</strong>
              <p>
                {{
                  sourceStates.shipmentAgent === 'ready'
                    ? `${shipmentAgentRuns.length} 条总编排运行 · ${shipmentAgentHumanCount} 项人工待办`
                    : '总 Agent 运行数据暂未确认'
                }}
              </p>
            </span>
            <UiIcon name="arrow" />
          </button>

          <button type="button" @click="router.push('/tradeops/shipment-cases')">
            <span class="professional-icon shipment"><UiIcon name="truck" /></span>
            <span>
              <small>跨域协同</small>
              <strong>发运案件中心</strong>
              <p>
                {{
                  sourceStates.shipment === 'ready'
                    ? `${openCases.length} 个未结案件 · ${unboundCases.length} 个待关联`
                    : '案件数据暂未确认'
                }}
              </p>
            </span>
            <UiIcon name="arrow" />
          </button>

          <button type="button" @click="router.push('/supply-chain/load-planning/tasks')">
            <span class="professional-icon load"><UiIcon name="box" /></span>
            <span>
              <small>SUPPLY CHAIN</small>
              <strong>智能装载工作台</strong>
              <p>
                {{
                  sourceStates.loadPlanning === 'ready'
                    ? `${loadPlanningTasks.length} 个任务 · 支持人工补充约束与方案审批`
                    : '装载任务数据暂未确认'
                }}
              </p>
            </span>
            <UiIcon name="arrow" />
          </button>

          <button type="button" @click="router.push('/link-booking/applications')">
            <span class="professional-icon link"><UiIcon name="file" /></span>
            <span>
              <small>CUSTOMS & BOOKING</small>
              <strong>LINK 七步订舱</strong>
              <p>
                {{
                  sourceStates.linkApplications === 'ready'
                    ? `${linkApplications.length} 份申请 · ${linkWaitingCount} 份等待承运人`
                    : '订舱申请数据暂未确认'
                }}
              </p>
            </span>
            <UiIcon name="arrow" />
          </button>
        </div>

        <div class="professional-note">
          <UiIcon name="shield" />
          <p>
            算法负责生成装载候选，业务人员负责确认安全约束与正式方案；驾驶舱不替代专业复核。
          </p>
        </div>
      </aside>
    </div>

    <div class="secondary-grid">
      <section class="dashboard-panel">
        <div class="panel-heading">
          <div>
            <span class="section-kicker">SHIPMENT PIPELINE</span>
            <h3>进行中的发运案件</h3>
            <p>案件只展示已真实绑定的装载与订舱资源数量。</p>
          </div>
          <button class="heading-link" type="button" @click="router.push('/tradeops/shipment-cases')">
            查看全部 <UiIcon name="arrow" />
          </button>
        </div>

        <div v-if="focusCases.length" class="case-table">
          <div class="case-table-head">
            <span>案件</span>
            <span>运输场景</span>
            <span>专业资源</span>
            <span>状态</span>
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
              <small>{{ shipmentCase.caseNumber }} · 更新 {{ formatDateTime(shipmentCase.updatedAt) }}</small>
            </span>
            <span>{{ scenarioLabels[shipmentCase.transportScenario] }}</span>
            <span class="resource-chips">
              <i :class="{ empty: resourceCount(shipmentCase, 'LOAD') === 0 }">
                装载 {{ resourceCount(shipmentCase, 'LOAD') }}
              </i>
              <i :class="{ empty: resourceCount(shipmentCase, 'LINK') === 0 }">
                订舱 {{ resourceCount(shipmentCase, 'LINK') }}
              </i>
            </span>
            <span class="case-status" :data-status="shipmentCase.status">
              {{ shipmentStatusLabels[shipmentCase.status] }}
            </span>
          </button>
        </div>
        <div
          v-else-if="sourceStates.shipment === 'error'"
          class="panel-empty compact-empty uncertain"
        >
          <span><UiIcon name="alert" /></span>
          <strong>发运案件来源不可用</strong>
          <p>当前不能判断是否存在未结案件，请先恢复数据通道。</p>
        </div>
        <div v-else class="panel-empty compact-empty">
          <span><UiIcon name="truck" /></span>
          <strong>暂无未结发运案件</strong>
          <p>可以从案件中心创建第一份跨域业务案件。</p>
        </div>
      </section>

      <section class="dashboard-panel agent-panel">
        <div class="panel-heading">
          <div>
            <span class="section-kicker">AGENT ACTIVITY</span>
            <h3>装载 Agent 运行快照</h3>
            <p>最近 {{ agentTaskSnapshots.length }} 个任务，状态来自服务端运行历史。</p>
          </div>
          <button
            class="heading-link"
            type="button"
            @click="router.push('/supply-chain/load-planning/tasks')"
          >
            进入工作台 <UiIcon name="arrow" />
          </button>
        </div>

        <div v-if="agentTaskSnapshots.length" class="agent-snapshot-list">
          <button
            v-for="snapshot in agentTaskSnapshots.slice(0, 5)"
            :key="snapshot.task.id"
            type="button"
            @click="router.push('/supply-chain/load-planning/tasks')"
          >
            <span class="agent-rail" :data-tone="agentStatusTone(snapshot)"></span>
            <span class="agent-task">
              <strong>{{ snapshot.task.taskName }}</strong>
              <small>{{ snapshot.task.taskNumber }} · {{ snapshot.task.cargoBoxCount }} 箱</small>
            </span>
            <span class="agent-state" :data-tone="agentStatusTone(snapshot)">
              {{ agentStatusLabel(snapshot) }}
            </span>
          </button>
        </div>
        <div
          v-else-if="sourceStates.loadPlanning === 'error'"
          class="panel-empty compact-empty uncertain"
        >
          <span><UiIcon name="alert" /></span>
          <strong>装载任务来源不可用</strong>
          <p>当前不能判断 Agent 是否有运行记录，请先恢复数据通道。</p>
        </div>
        <div v-else class="panel-empty compact-empty">
          <span><UiIcon name="layers" /></span>
          <strong>暂无装载任务运行</strong>
          <p>创建任务后，Agent 才会生成可恢复的运行记录。</p>
        </div>

        <p v-if="agentUnavailableCount" class="agent-footnote">
          <UiIcon name="alert" />
          {{ agentUnavailableCount }} 个最近任务的运行历史暂未同步，未计入待处理数量。
        </p>
      </section>
    </div>

    <section class="system-strip" aria-label="平台与业务数据通道状态">
      <div class="system-strip-heading">
        <span class="section-kicker">SYSTEM STATUS</span>
        <h3>平台与数据通道</h3>
        <p>健康信息放在次级区域，不干扰业务待办判断。</p>
      </div>

      <div class="system-status-list">
        <div>
          <span class="system-icon"><UiIcon name="server" /></span>
          <span>
            <small>Java Platform BFF</small>
            <strong>{{ platformHealth?.service ?? 'platform-bff' }}</strong>
          </span>
          <i :class="{ online: platformOnline }">{{ platformOnline ? '运行中' : '不可用' }}</i>
        </div>

        <div>
          <span class="system-icon"><UiIcon name="truck" /></span>
          <span>
            <small>Shipment Case 数据通道</small>
            <strong>
              {{
                sourceStates.shipment === 'ready'
                  ? `${shipmentCases.length} 条案件快照`
                  : '快照数量未知'
              }}
            </strong>
          </span>
          <i :class="{ online: sourceStates.shipment === 'ready' }">
            {{ serviceStateLabel(sourceStates.shipment) }}
          </i>
        </div>

        <div>
          <span class="system-icon"><UiIcon name="server" /></span>
          <span>
            <small>Shipment Agent Runtime</small>
            <strong>
              {{
                sourceStates.shipmentAgent === 'ready'
                  ? `${shipmentAgentRuns.length} 条运行快照`
                  : '运行数量未知'
              }}
            </strong>
          </span>
          <i :class="{ online: sourceStates.shipmentAgent === 'ready' }">
            {{ serviceStateLabel(sourceStates.shipmentAgent) }}
          </i>
        </div>

        <div>
          <span class="system-icon"><UiIcon name="layers" /></span>
          <span>
            <small>Load Planning 数据通道</small>
            <strong>
              {{
                sourceStates.loadPlanning === 'ready'
                  ? `${loadPlanningTasks.length} 条任务摘要`
                  : '任务数量未知'
              }}
            </strong>
          </span>
          <i :class="{ online: sourceStates.loadPlanning === 'ready' }">
            {{ serviceStateLabel(sourceStates.loadPlanning) }}
          </i>
        </div>

        <div>
          <span class="system-icon"><UiIcon name="database" /></span>
          <span>
            <small>LINK Booking Service</small>
            <strong>{{ linkHealth?.environment ?? '状态未知' }} · Python</strong>
          </span>
          <i :class="{ online: linkOnline }">
            {{
              sourceStates.linkHealth === 'idle'
                ? '尚未同步'
                : linkOnline
                  ? '运行中'
                  : '不可用'
            }}
          </i>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.operations-dashboard {
  display: grid;
  gap: 18px;
  color: #17211d;
}

.metric-grid {
  grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
}

button {
  font: inherit;
}

.dashboard-hero {
  position: relative;
  display: grid;
  min-height: 268px;
  grid-template-columns: minmax(0, 1.7fr) minmax(260px, 0.75fr);
  gap: 36px;
  overflow: hidden;
  padding: 34px 38px;
  border: 1px solid rgba(36, 79, 66, 0.54);
  border-radius: 20px;
  color: #f7fbf9;
  background:
    radial-gradient(circle at 88% 18%, rgba(91, 171, 139, 0.24), transparent 33%),
    radial-gradient(circle at 58% 120%, rgba(113, 149, 214, 0.18), transparent 38%),
    linear-gradient(135deg, #172b25 0%, #1e4035 53%, #142b25 100%);
  box-shadow: 0 18px 45px rgba(28, 57, 48, 0.14);
}

.dashboard-hero::after {
  position: absolute;
  top: -74px;
  right: -42px;
  width: 250px;
  height: 250px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 50%;
  box-shadow:
    0 0 0 28px rgba(255, 255, 255, 0.025),
    0 0 0 68px rgba(255, 255, 255, 0.018);
  content: "";
  pointer-events: none;
}

.hero-copy,
.hero-status {
  position: relative;
  z-index: 1;
}

.hero-copy {
  align-self: center;
}

.hero-kicker,
.section-kicker {
  display: block;
  color: #7aa993;
  font-size: 9px;
  font-weight: 750;
  letter-spacing: 0.16em;
}

.hero-kicker {
  color: #9ed7bc;
}

.hero-copy h2 {
  margin: 11px 0 10px;
  font-size: clamp(25px, 3vw, 37px);
  font-weight: 680;
  letter-spacing: -0.045em;
}

.hero-copy > p {
  max-width: 620px;
  margin: 0;
  color: rgba(240, 249, 245, 0.73);
  font-size: 12px;
  line-height: 1.85;
}

.hero-actions {
  display: flex;
  gap: 10px;
  margin-top: 25px;
}

.hero-actions button {
  display: inline-flex;
  min-height: 39px;
  align-items: center;
  gap: 8px;
  padding: 0 15px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
}

.hero-actions .ui-icon {
  width: 16px;
  height: 16px;
}

.hero-primary {
  border: 1px solid #b8e4cf;
  color: #173b2f;
  background: #b8e4cf;
}

.hero-primary:hover {
  background: #cef0df;
}

.hero-secondary {
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #f3faf6;
  background: rgba(255, 255, 255, 0.07);
}

.hero-secondary:hover {
  background: rgba(255, 255, 255, 0.12);
}

.hero-status {
  display: grid;
  align-content: center;
  justify-items: center;
  gap: 16px;
}

.attention-orb {
  display: grid;
  width: 156px;
  height: 156px;
  place-content: center;
  border: 1px solid rgba(255, 205, 138, 0.3);
  border-radius: 50%;
  text-align: center;
  background:
    radial-gradient(circle, rgba(226, 167, 86, 0.18), rgba(226, 167, 86, 0.03) 63%, transparent 64%);
  box-shadow:
    inset 0 0 36px rgba(240, 180, 94, 0.08),
    0 0 0 8px rgba(255, 255, 255, 0.025);
}

.attention-orb.clear {
  border-color: rgba(146, 215, 181, 0.32);
  background:
    radial-gradient(circle, rgba(95, 181, 139, 0.18), rgba(95, 181, 139, 0.03) 63%, transparent 64%);
}

.attention-orb.partial {
  border-color: rgba(244, 196, 119, 0.42);
  background:
    radial-gradient(circle, rgba(221, 157, 67, 0.2), rgba(221, 157, 67, 0.035) 63%, transparent 64%);
}

.attention-orb span,
.attention-orb small {
  color: rgba(241, 249, 245, 0.65);
  font-size: 9px;
}

.attention-orb strong {
  margin: 2px 0;
  color: #ffffff;
  font-size: 42px;
  font-weight: 620;
  letter-spacing: -0.06em;
}

.sync-state {
  display: flex;
  align-items: center;
  gap: 9px;
  color: rgba(241, 249, 245, 0.68);
}

.sync-state > span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #d17b6e;
  box-shadow: 0 0 0 4px rgba(209, 123, 110, 0.12);
}

.sync-state > span.online {
  background: #83d0aa;
  box-shadow: 0 0 0 4px rgba(131, 208, 170, 0.12);
}

.sync-state > div {
  display: grid;
  gap: 2px;
}

.sync-state small {
  font-size: 8px;
}

.sync-state strong {
  color: #f2faf6;
  font-size: 10px;
  font-weight: 650;
}

.sync-state button {
  display: grid;
  width: 30px;
  height: 30px;
  margin-left: 4px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  color: #d9ede3;
  background: rgba(255, 255, 255, 0.05);
  cursor: pointer;
}

.sync-state button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.sync-state button:not(:disabled):hover {
  background: rgba(255, 255, 255, 0.12);
}

.sync-state .ui-icon {
  width: 14px;
  height: 14px;
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
}

.degraded-banner div {
  flex: 1;
}

.degraded-banner strong {
  display: block;
  font-size: 11px;
}

.degraded-banner p {
  margin: 3px 0 0;
  color: #8d7443;
  font-size: 9px;
}

.degraded-banner button {
  border: 0;
  color: #765b27;
  background: transparent;
  cursor: pointer;
  font-size: 9px;
  font-weight: 700;
}

.operation-metric {
  position: relative;
  display: grid;
  min-width: 0;
  min-height: 132px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 13px;
  align-items: center;
  padding: 18px;
  border: 1px solid #e2e8e5;
  border-radius: 15px;
  color: inherit;
  text-align: left;
  background: #ffffff;
  cursor: pointer;
  transition:
    transform 160ms ease,
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.operation-metric::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  border-radius: 15px 0 0 15px;
  background: #5b927e;
  content: "";
}

.operation-metric.warning::before {
  background: #d59a4c;
}

.operation-metric.agent::before {
  background: #677eae;
}

.operation-metric.orchestrator::before {
  background: #2e8068;
}

.operation-metric.booking::before {
  background: #7d6ba0;
}

.operation-metric:hover {
  transform: translateY(-2px);
  border-color: #cddbd5;
  box-shadow: 0 10px 24px rgba(46, 75, 65, 0.08);
}

.operation-metric .metric-icon {
  display: grid;
  width: 39px;
  height: 39px;
  place-items: center;
  border-radius: 11px;
  color: #41715e;
  background: #eaf4ef;
}

.operation-metric.warning .metric-icon {
  color: #a46d28;
  background: #fdf3e3;
}

.operation-metric.agent .metric-icon {
  color: #536b9e;
  background: #edf1fa;
}

.operation-metric.orchestrator .metric-icon {
  color: #276d59;
  background: #e6f3ee;
}

.operation-metric.booking .metric-icon {
  color: #6d598f;
  background: #f2eef8;
}

.operation-metric .metric-icon .ui-icon {
  width: 18px;
  height: 18px;
}

.metric-copy {
  min-width: 0;
}

.metric-copy small {
  display: block;
  color: #79817d;
  font-size: 9px;
}

.metric-copy strong {
  display: block;
  margin-top: 3px;
  color: #19231f;
  font-size: 27px;
  font-weight: 660;
  letter-spacing: -0.045em;
}

.metric-copy p {
  overflow: hidden;
  margin: 5px 0 0;
  color: #909793;
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-arrow {
  width: 14px;
  height: 14px;
  color: #adb4b0;
}

.primary-grid,
.secondary-grid {
  display: grid;
  gap: 14px;
}

.primary-grid {
  grid-template-columns: minmax(0, 1.68fr) minmax(290px, 0.72fr);
}

.secondary-grid {
  grid-template-columns: minmax(0, 1.35fr) minmax(330px, 0.85fr);
}

.dashboard-panel,
.professional-panel,
.system-strip {
  overflow: hidden;
  border: 1px solid #e2e7e4;
  border-radius: 16px;
  background: #ffffff;
}

.panel-heading {
  display: flex;
  min-height: 79px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 17px 19px;
  border-bottom: 1px solid #edf0ee;
}

.panel-heading.compact {
  min-height: 74px;
}

.panel-heading h3,
.system-strip-heading h3 {
  margin: 5px 0 0;
  color: #1e2924;
  font-size: 14px;
  font-weight: 680;
}

.panel-heading p,
.system-strip-heading p {
  margin: 4px 0 0;
  color: #929995;
  font-size: 9px;
}

.panel-count {
  display: grid;
  min-width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 9px;
  color: #8a622e;
  background: #fcf1df;
  font-size: 11px;
  font-weight: 700;
}

.panel-loading {
  display: flex;
  min-height: 280px;
  align-items: center;
  justify-content: center;
  gap: 9px;
  color: #8b938f;
  font-size: 10px;
}

.panel-loading span {
  width: 10px;
  height: 10px;
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
  padding: 4px 18px;
}

.work-item {
  display: grid;
  width: 100%;
  min-height: 79px;
  grid-template-columns: 7px minmax(0, 1fr) auto;
  gap: 13px;
  align-items: center;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: inherit;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.work-item:last-of-type {
  border-bottom: 0;
}

.work-item:hover .work-main strong,
.work-item:hover .work-meta > span {
  color: #2f6b57;
}

.work-marker {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7e9baa;
  box-shadow: 0 0 0 4px rgba(126, 155, 170, 0.12);
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
  color: #8c9590;
  font-size: 8px;
}

.work-main strong {
  display: block;
  overflow: hidden;
  margin-top: 3px;
  color: #24302b;
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-main p {
  overflow: hidden;
  margin: 4px 0 0;
  color: #7e8782;
  font-size: 9px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-meta {
  display: grid;
  min-width: 100px;
  justify-items: end;
  gap: 9px;
}

.work-meta time {
  color: #a0a7a3;
  font-size: 8px;
}

.work-meta > span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: #547969;
  font-size: 8px;
  font-weight: 700;
}

.work-meta .ui-icon,
.heading-link .ui-icon {
  width: 12px;
  height: 12px;
}

.queue-more {
  width: 100%;
  padding: 11px;
  border: 0;
  border-top: 1px solid #eff1f0;
  color: #587566;
  background: #fbfcfb;
  cursor: pointer;
  font-size: 8px;
}

.panel-empty {
  display: grid;
  min-height: 280px;
  place-content: center;
  justify-items: center;
  padding: 28px;
  text-align: center;
}

.panel-empty > span {
  display: grid;
  width: 40px;
  height: 40px;
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
  margin-top: 11px;
  color: #36423d;
  font-size: 11px;
}

.panel-empty p {
  max-width: 320px;
  margin: 5px 0 0;
  color: #969d99;
  font-size: 9px;
  line-height: 1.6;
}

.compact-empty {
  min-height: 220px;
}

.professional-panel {
  background:
    linear-gradient(180deg, rgba(244, 248, 246, 0.65), transparent 110px),
    #ffffff;
}

.professional-list {
  padding: 7px 14px 2px;
}

.professional-list button {
  display: grid;
  width: 100%;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 11px;
  align-items: center;
  padding: 13px 4px;
  border: 0;
  border-bottom: 1px solid #edf0ee;
  color: inherit;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.professional-list button:hover strong {
  color: #326c58;
}

.professional-list button > .ui-icon {
  width: 13px;
  height: 13px;
  color: #a4aaa7;
}

.professional-icon {
  display: grid;
  width: 37px;
  height: 37px;
  place-items: center;
  border-radius: 11px;
}

.professional-icon.shipment {
  color: #3f755f;
  background: #e8f3ee;
}

.professional-icon.agent {
  color: #1f6e57;
  background: #e1f1eb;
}

.professional-icon.load {
  color: #546b9b;
  background: #ecf0f9;
}

.professional-icon.link {
  color: #725e91;
  background: #f1edf7;
}

.professional-icon .ui-icon {
  width: 17px;
  height: 17px;
}

.professional-list button > span:nth-child(2) {
  min-width: 0;
}

.professional-list small {
  display: block;
  color: #99a19d;
  font-size: 7px;
  font-weight: 700;
  letter-spacing: 0.07em;
}

.professional-list strong {
  display: block;
  margin-top: 3px;
  color: #2a3631;
  font-size: 11px;
  font-weight: 650;
}

.professional-list p {
  overflow: hidden;
  margin: 4px 0 0;
  color: #8f9692;
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.professional-note {
  display: flex;
  gap: 9px;
  margin: 12px 14px 15px;
  padding: 11px 12px;
  border: 1px solid #e6ece9;
  border-radius: 11px;
  color: #69756f;
  background: #f7f9f8;
}

.professional-note .ui-icon {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  color: #517763;
}

.professional-note p {
  margin: 0;
  font-size: 8px;
  line-height: 1.6;
}

.heading-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  border: 0;
  color: #587768;
  background: transparent;
  cursor: pointer;
  font-size: 8px;
  font-weight: 700;
}

.case-table {
  padding: 0 18px 7px;
}

.case-table-head,
.case-row {
  display: grid;
  grid-template-columns: minmax(190px, 1.45fr) minmax(90px, 0.72fr) minmax(130px, 0.8fr) 68px;
  gap: 12px;
  align-items: center;
}

.case-table-head {
  min-height: 38px;
  border-bottom: 1px solid #edf0ee;
  color: #9aa19d;
  font-size: 8px;
}

.case-row {
  width: 100%;
  min-height: 65px;
  padding: 7px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: #59645f;
  text-align: left;
  background: transparent;
  cursor: pointer;
  font-size: 9px;
}

.case-row:last-child {
  border-bottom: 0;
}

.case-row:hover .case-identity strong {
  color: #2f6b57;
}

.case-identity {
  min-width: 0;
}

.case-identity strong,
.case-identity small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-identity strong {
  color: #29352f;
  font-size: 10px;
  font-weight: 650;
}

.case-identity small {
  margin-top: 4px;
  color: #9aa19d;
  font-size: 8px;
}

.resource-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.resource-chips i {
  padding: 3px 6px;
  border-radius: 5px;
  color: #4f735f;
  background: #edf5f1;
  font-size: 7px;
  font-style: normal;
}

.resource-chips i.empty {
  color: #9a8058;
  background: #faf3e8;
}

.case-status {
  justify-self: start;
  padding: 4px 7px;
  border-radius: 999px;
  color: #53705f;
  background: #edf4f0;
  font-size: 7px;
  font-weight: 700;
}

.case-status[data-status="DRAFT"] {
  color: #846b43;
  background: #faf2e4;
}

.agent-snapshot-list {
  padding: 5px 17px;
}

.agent-snapshot-list button {
  display: grid;
  width: 100%;
  min-height: 57px;
  grid-template-columns: 3px minmax(0, 1fr) auto;
  gap: 11px;
  align-items: center;
  padding: 7px 0;
  border: 0;
  border-bottom: 1px solid #eff1f0;
  color: inherit;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.agent-snapshot-list button:last-child {
  border-bottom: 0;
}

.agent-snapshot-list button:hover .agent-task strong {
  color: #326c58;
}

.agent-rail {
  width: 3px;
  height: 27px;
  border-radius: 3px;
  background: #a6afaa;
}

.agent-rail[data-tone="waiting"] {
  background: #d4a253;
}

.agent-rail[data-tone="danger"] {
  background: #ca6d63;
}

.agent-rail[data-tone="success"] {
  background: #5c987d;
}

.agent-task {
  min-width: 0;
}

.agent-task strong,
.agent-task small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-task strong {
  color: #2a3631;
  font-size: 10px;
  font-weight: 650;
}

.agent-task small {
  margin-top: 4px;
  color: #9ba29e;
  font-size: 8px;
}

.agent-state {
  padding: 4px 7px;
  border-radius: 999px;
  color: #737d78;
  background: #f0f2f1;
  font-size: 7px;
  font-weight: 700;
  white-space: nowrap;
}

.agent-state[data-tone="waiting"] {
  color: #8c6a32;
  background: #faf1df;
}

.agent-state[data-tone="danger"] {
  color: #99534d;
  background: #fbebea;
}

.agent-state[data-tone="success"] {
  color: #47745f;
  background: #eaf4ef;
}

.agent-footnote {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 17px 13px;
  padding: 8px 10px;
  border-radius: 8px;
  color: #8a6c3d;
  background: #fbf4e8;
  font-size: 8px;
}

.agent-footnote .ui-icon {
  width: 13px;
  height: 13px;
}

.system-strip {
  display: grid;
  grid-template-columns: minmax(190px, 0.7fr) minmax(0, 2.3fr);
  gap: 24px;
  align-items: center;
  padding: 18px 20px;
}

.system-strip-heading h3 {
  font-size: 13px;
}

.system-status-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 9px;
}

.system-status-list > div {
  display: grid;
  min-width: 0;
  min-height: 57px;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 9px;
  align-items: center;
  padding: 9px 10px;
  border: 1px solid #edf0ee;
  border-radius: 10px;
  background: #fafbfa;
}

.system-icon {
  display: grid;
  width: 29px;
  height: 29px;
  place-items: center;
  border-radius: 8px;
  color: #65736c;
  background: #eef1ef;
}

.system-icon .ui-icon {
  width: 14px;
  height: 14px;
}

.system-status-list > div > span:nth-child(2) {
  min-width: 0;
}

.system-status-list small,
.system-status-list strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.system-status-list small {
  color: #959c98;
  font-size: 7px;
}

.system-status-list strong {
  margin-top: 3px;
  color: #3a4640;
  font-size: 8px;
  font-weight: 650;
}

.system-status-list i {
  grid-column: 1 / -1;
  color: #a35c54;
  font-size: 7px;
  font-style: normal;
}

.system-status-list i::before {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 5px;
  border-radius: 50%;
  background: currentColor;
  content: "";
}

.system-status-list i.online {
  color: #4d8069;
}

@media (max-width: 1060px) {
  .dashboard-hero {
    grid-template-columns: minmax(0, 1.45fr) minmax(220px, 0.55fr);
  }

  .primary-grid,
  .secondary-grid {
    grid-template-columns: 1fr;
  }

  .system-strip {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 820px) {
  .dashboard-hero {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .hero-status {
    grid-template-columns: auto 1fr;
    justify-items: start;
  }

  .attention-orb {
    width: 118px;
    height: 118px;
  }

  .attention-orb strong {
    font-size: 32px;
  }

  .sync-state {
    align-self: center;
  }

  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .system-status-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .dashboard-hero {
    padding: 27px 23px;
  }

  .hero-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .hero-actions button {
    width: 100%;
    justify-content: center;
  }

  .hero-status {
    grid-template-columns: 1fr;
    justify-items: center;
  }

  .metric-grid,
  .system-status-list {
    grid-template-columns: 1fr;
  }

  .operation-metric {
    min-height: 112px;
  }

  .degraded-banner {
    align-items: flex-start;
  }

  .degraded-banner button {
    display: none;
  }

  .work-item {
    grid-template-columns: 7px minmax(0, 1fr);
  }

  .work-meta {
    display: none;
  }

  .case-table-head {
    display: none;
  }

  .case-row {
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
  }

  .case-row > span:nth-child(2),
  .resource-chips {
    display: none;
  }

  .system-strip {
    padding: 17px;
  }
}
</style>
