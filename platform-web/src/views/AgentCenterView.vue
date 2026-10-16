<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getShipmentAgentRuns,
  type ShipmentAgentRun,
  type ShipmentAgentStage,
  type ShipmentAgentRunStatus,
} from '@/api/platform'
import ShipmentAgentTrace from '@/components/shipment-agent/ShipmentAgentTrace.vue'
import ShipmentAgentWorkstreams from '@/components/shipment-agent/ShipmentAgentWorkstreams.vue'
import UiIcon from '@/components/UiIcon.vue'

type RunFilter =
  | 'ALL'
  | 'PROCESSING'
  | 'WAITING_HUMAN'
  | 'WAITING_RESOURCE'
  | 'WAITING_EXTERNAL'
  | 'FAILED'
  | 'COMPLETED'

type FilterIcon =
  | 'layers'
  | 'refresh'
  | 'users'
  | 'box'
  | 'clock'
  | 'alert'
  | 'check'

interface RunFilterOption {
  key: RunFilter
  label: string
  description: string
  icon: FilterIcon
  tone: string
  count: number
}

const route = useRoute()
const router = useRouter()
const runs = ref<ShipmentAgentRun[]>([])
const selectedRunId = ref('')
const loading = ref(true)
const hasLoaded = ref(false)
const loadError = ref('')
const lastRefreshedAt = ref<Date | null>(null)
const runFilter = ref<RunFilter>('ALL')
const keyword = ref('')
const autoRefreshEnabled = ref(true)
const pageVisible = ref(true)
let autoRefreshTimer: number | undefined

const statusLabels: Record<ShipmentAgentRunStatus, string> = {
  RUNNING: '自动处理中',
  WAITING_HUMAN: '等待业务处理',
  WAITING_RESOURCE: '等待专业作业',
  WAITING_EXTERNAL: '等待外部回复',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已停止',
}

const stageLabels: Record<ShipmentAgentStage, string> = {
  SYNCING_CASE: '读取案件资料',
  AWAITING_SCENARIO_POLICY: '确认本票处理范围',
  ASSESSING_DATA_READINESS: '核验资料与库存',
  AWAITING_DATA_REMEDIATION: '等待补充资料与库存',
  AWAITING_RESOURCE_BINDING: '等待关联专业作业',
  AWAITING_RESOURCE_SELECTION: '确认本次专业作业',
  AWAITING_CASE_ACTIVATION: '等待激活案件',
  STARTING_LOAD_AGENT: '启动智能装载',
  AWAITING_LOAD_CONSTRAINTS: '补充装载约束',
  AWAITING_LOAD_REVIEW: '复核装载方案',
  AWAITING_LOAD_RELEASE: '发布正式装载版本',
  AWAITING_LOAD_REPLAN: '重新规划装载方案',
  AWAITING_BOOKING_SUBMIT_APPROVAL: '审批 LINK 提交',
  SUBMITTING_BOOKING: '提交 LINK 申请',
  AWAITING_BOOKING_RESULT: '等待承运人结果',
  AWAITING_BOOKING_RETRY_APPROVAL: '审批订舱重试',
  AWAITING_BOOKING_REPLACEMENT: '替换异常订舱申请',
  AWAITING_COMPLETION_APPROVAL: '审批案件结案',
  COMPLETING_CASE: '执行案件结案',
  COMPLETED: '流程已完成',
  FAILED: '流程运行失败',
  CANCELLED: '流程已停止',
}

const scenarioLabels: Record<ShipmentAgentRun['transportScenario'], string> = {
  UNDECIDED: '运输方式待确认',
  SEA_FCL: '海运整柜',
  SEA_LCL: '海运拼箱',
  AIR: '空运',
  EXPRESS: '国际快递',
  LOAD_PLANNING_ONLY: '仅装载规划',
  BOOKING_ONLY: '仅订舱',
}

const caseStatusLabels: Record<ShipmentAgentRun['caseStatus'], string> = {
  DRAFT: '草稿',
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

const sortedRuns = computed(() =>
  [...runs.value].sort(
    (left, right) =>
      new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  ),
)

function matchesRunFilter(run: ShipmentAgentRun, filter: RunFilter): boolean {
  if (filter === 'ALL') return true
  if (filter === 'PROCESSING') {
    return run.status === 'RUNNING'
  }
  return run.status === filter
}

const filteredRuns = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  return sortedRuns.value.filter((run) => {
    const matchesStatus = matchesRunFilter(run, runFilter.value)
    const matchesKeyword =
      !query ||
      run.caseNumber.toLocaleLowerCase().includes(query) ||
      run.caseName.toLocaleLowerCase().includes(query) ||
      run.runId.toLocaleLowerCase().includes(query) ||
      run.currentStage.toLocaleLowerCase().includes(query) ||
      stageLabels[run.currentStage].toLocaleLowerCase().includes(query)
    return matchesStatus && matchesKeyword
  })
})

const selectedRun = computed(
  () => filteredRuns.value.find((run) => run.runId === selectedRunId.value) ?? null,
)

const filterOptions = computed<RunFilterOption[]>(() => {
  const count = (filter: RunFilter) =>
    runs.value.filter((run) => matchesRunFilter(run, filter)).length

  return [
    {
      key: 'ALL',
      label: '全部',
      description: '包含停止的历史运行',
      icon: 'layers',
      tone: 'all',
      count: count('ALL'),
    },
    {
      key: 'PROCESSING',
      label: '处理中',
      description: '系统正在自动执行',
      icon: 'refresh',
      tone: 'processing',
      count: count('PROCESSING'),
    },
    {
      key: 'WAITING_HUMAN',
      label: '待业务处理',
      description: '等待补数、审批或确认',
      icon: 'users',
      tone: 'human',
      count: count('WAITING_HUMAN'),
    },
    {
      key: 'WAITING_RESOURCE',
      label: '待专业作业',
      description: '等待装载或订舱处理',
      icon: 'box',
      tone: 'resource',
      count: count('WAITING_RESOURCE'),
    },
    {
      key: 'WAITING_EXTERNAL',
      label: '等待外部',
      description: '等待承运人回复',
      icon: 'clock',
      tone: 'external',
      count: count('WAITING_EXTERNAL'),
    },
    {
      key: 'FAILED',
      label: '失败',
      description: '需要排查或恢复',
      icon: 'alert',
      tone: 'failure',
      count: count('FAILED'),
    },
    {
      key: 'COMPLETED',
      label: '已完成',
      description: '已经闭环的历史运行',
      icon: 'check',
      tone: 'completed',
      count: count('COMPLETED'),
    },
  ]
})

function queryRunId(): string {
  return typeof route.query.runId === 'string' ? route.query.runId.trim() : ''
}

function syncRunIdToUrl(runId: string): void {
  const currentRunId = queryRunId()
  const hasMalformedRunId =
    route.query.runId !== undefined && typeof route.query.runId !== 'string'
  if (currentRunId === runId && !hasMalformedRunId) return

  const query = { ...route.query }
  if (runId) {
    query.runId = runId
  } else {
    delete query.runId
  }
  void router.replace({ query })
}

function setSelectedRun(runId: string, syncUrl = true): void {
  selectedRunId.value = runId
  if (syncUrl) syncRunIdToUrl(runId)
}

/**
 * 选中项必须来自当前筛选结果。
 *
 * 这样切换筛选、输入搜索词或刷新数据后，右侧 Inspector 不会继续展示一条已经从
 * 左侧队列消失的旧运行；URL 中的 runId 也会同步为当前真正可见的选中项。
 */
function reconcileSelection(preferredRunId = ''): void {
  const visibleRuns = filteredRuns.value
  const preferred = visibleRuns.find((run) => run.runId === preferredRunId)
  const current = visibleRuns.find((run) => run.runId === selectedRunId.value)
  const nextRunId = preferred?.runId ?? current?.runId ?? visibleRuns[0]?.runId ?? ''
  setSelectedRun(nextRunId)
}

async function loadRuns(): Promise<void> {
  if (loading.value && hasLoaded.value) return
  loading.value = true
  loadError.value = ''
  try {
    runs.value = await getShipmentAgentRuns()
    hasLoaded.value = true
    reconcileSelection(queryRunId())
    lastRefreshedAt.value = new Date()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取自动化运行记录失败'
  } finally {
    loading.value = false
  }
}

function showFilter(filter: RunFilter): void {
  runFilter.value = filter
  reconcileSelection()
}

function selectRun(runId: string): void {
  setSelectedRun(runId)
}

function toggleAutoRefresh(): void {
  autoRefreshEnabled.value = !autoRefreshEnabled.value
  if (autoRefreshEnabled.value && pageVisible.value) {
    void loadRuns()
  }
}

function handleVisibilityChange(): void {
  pageVisible.value = !document.hidden
  if (pageVisible.value && autoRefreshEnabled.value) {
    void loadRuns()
  }
}

function openCase(caseNumber: string): void {
  void router.push({
    name: 'shipment-case-workspace',
    params: { caseNumber },
  })
}

function dataReadinessLabel(run: ShipmentAgentRun): string {
  const labels: Record<string, string> = {
    LEGACY_BYPASSED: '历史运行未检查',
    NOT_EVALUATED: '等待检查',
    ACTION_REQUIRED: '需要补充数据',
    READY: '业务数据已就绪',
    DEMO_READY: '演示数据已就绪',
  }
  const status = run.dataReadiness?.status
  return status ? labels[status] ?? status : '尚未评估'
}

function humanTaskOwner(run: ShipmentAgentRun): string {
  return run.currentStage === 'AWAITING_DATA_REMEDIATION'
    ? '仓库业务员'
    : '案件业务负责人'
}

function openNotificationCount(run: ShipmentAgentRun): number {
  return (run.notifications ?? []).filter(
    (notification) => notification.status === 'OPEN',
  ).length
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

function formatOptionalDateTime(value: string | null): string {
  return value ? formatDateTime(value) : '未安排'
}

function formatRefreshTime(value: Date | null): string {
  if (!value) return '尚未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(value)
}

watch(keyword, () => {
  reconcileSelection()
})

watch(
  () => route.query.runId,
  () => {
    if (!hasLoaded.value) return

    const requestedRunId = queryRunId()
    const requestedRun = runs.value.find((run) => run.runId === requestedRunId)
    if (requestedRun) {
      // 外部深链优先可达：如果当前筛选挡住了目标运行，回到“全部”并清空搜索词。
      if (!filteredRuns.value.some((run) => run.runId === requestedRunId)) {
        runFilter.value = 'ALL'
        keyword.value = ''
      }
      setSelectedRun(requestedRunId, false)
      return
    }

    // 非法、过期或数组形式的 runId 不能滞留在地址栏。
    reconcileSelection()
  },
)

onMounted(() => {
  pageVisible.value = !document.hidden
  document.addEventListener('visibilitychange', handleVisibilityChange)
  autoRefreshTimer = window.setInterval(() => {
    if (autoRefreshEnabled.value && pageVisible.value && !loading.value) {
      void loadRuns()
    }
  }, 30_000)
  void loadRuns()
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  if (autoRefreshTimer !== undefined) {
    window.clearInterval(autoRefreshTimer)
  }
})
</script>

<template>
  <section class="agent-center">
    <header class="agent-center-hero">
      <div>
        <div class="agent-center-breadcrumb">
          <span>TradeOps</span>
          <i></i>
          <strong>运营监控</strong>
        </div>
        <h2>自动化运行监控</h2>
        <p>供主管、平台运营和审计人员查看自动处理、人工接管、外部等待与失败恢复。</p>
      </div>
      <div class="agent-center-actions">
        <div>
          <span>最近同步</span>
          <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
        </div>
        <button
          class="auto-refresh-toggle"
          type="button"
          :class="{ active: autoRefreshEnabled }"
          :aria-pressed="autoRefreshEnabled"
          @click="toggleAutoRefresh"
        >
          <i></i>
          30 秒自动刷新
        </button>
        <button type="button" :disabled="loading" @click="loadRuns">
          <UiIcon name="refresh" />
          {{ loading ? '同步中' : '刷新运行' }}
        </button>
      </div>
    </header>

    <div v-if="loadError" class="agent-center-error" role="alert">
      <span>!</span>
      <div>
        <strong>运行数据加载失败</strong>
        <p>{{ loadError }}</p>
      </div>
      <button type="button" @click="loadRuns">重新加载</button>
    </div>

    <section class="agent-center-kpis" aria-label="自动化运行指标">
      <button
        v-for="option in filterOptions"
        :key="option.key"
        type="button"
        :class="{ active: runFilter === option.key }"
        @click="showFilter(option.key)"
      >
        <span :class="option.tone"><UiIcon :name="option.icon" /></span>
        <div>
          <small>{{ option.label }}</small>
          <strong>{{ option.count }}</strong>
          <em>{{ option.description }}</em>
        </div>
      </button>
    </section>

    <div class="agent-center-toolbar">
      <label>
        <UiIcon name="search" />
        <input
          v-model="keyword"
          type="search"
          placeholder="搜索案件名称、案件编号、运行 ID 或业务阶段"
        />
      </label>
      <span>
        {{ filteredRuns.length }} / {{ runs.length }} 条运行
      </span>
    </div>

    <div v-if="loading && runs.length === 0" class="agent-center-loading">
      <span></span>
      <div>
        <strong>正在汇总自动化运行</strong>
        <p>读取流程状态、业务环节和处理记录。</p>
      </div>
    </div>

    <div v-else-if="runs.length === 0" class="agent-center-empty">
      <UiIcon name="server" />
      <strong>还没有自动化运行记录</strong>
      <p>从一票发运案件启动智能协同后，这里会保留处理状态和完整记录。</p>
      <RouterLink to="/tradeops/shipment-cases">进入发运案件中心</RouterLink>
    </div>

    <div v-else class="agent-center-grid" :class="{ 'single-panel': !selectedRun }">
      <section class="run-list-panel">
        <div class="run-list-heading">
          <div>
            <span>AUTOMATION RUNS</span>
            <h3>自动处理记录</h3>
          </div>
          <span>按最近更新时间排序</span>
        </div>

        <div v-if="filteredRuns.length" class="run-list">
          <button
            v-for="item in filteredRuns"
            :key="item.runId"
            type="button"
            :class="{ selected: selectedRun?.runId === item.runId }"
            @click="selectRun(item.runId)"
          >
            <span class="run-status-dot" :data-status="item.status"></span>
            <div class="run-list-copy">
              <span>{{ item.caseName }}</span>
              <strong>{{ stageLabels[item.currentStage] }}</strong>
              <small>{{ item.caseNumber }} · {{ scenarioLabels[item.transportScenario] }}</small>
            </div>
            <div class="run-list-state">
              <strong>{{ statusLabels[item.status] }}</strong>
              <time>{{ formatDateTime(item.updatedAt) }}</time>
            </div>
          </button>
        </div>
        <div v-else class="run-filter-empty">
          当前筛选条件下没有运行。
        </div>
      </section>

      <section v-if="selectedRun" class="run-inspector">
        <header class="run-inspector-heading">
          <div>
            <span>运行详情</span>
            <h3>{{ selectedRun.caseNumber }}</h3>
            <small>{{ selectedRun.caseName }}</small>
          </div>
          <button type="button" @click="openCase(selectedRun.caseNumber)">
            打开案件工作台
            <UiIcon name="arrow" />
          </button>
        </header>

        <div class="run-inspector-facts">
          <div>
            <span>状态</span>
            <strong>{{ statusLabels[selectedRun.status] }}</strong>
          </div>
          <div>
            <span>当前业务阶段</span>
            <strong>{{ stageLabels[selectedRun.currentStage] }}</strong>
          </div>
          <div>
            <span>运输场景</span>
            <strong>{{ scenarioLabels[selectedRun.transportScenario] }}</strong>
          </div>
          <div>
            <span>案件状态</span>
            <strong>{{ caseStatusLabels[selectedRun.caseStatus] }}</strong>
          </div>
          <div>
            <span>资料与库存</span>
            <strong>{{ dataReadinessLabel(selectedRun) }}</strong>
          </div>
          <div>
            <span>阻塞问题</span>
            <strong>
              {{
                selectedRun.dataReadiness?.blockingIssueCount
                  ?? selectedRun.dataReadiness?.issueCounts?.blocking
                  ?? selectedRun.dataReadiness?.issues.length
                  ?? 0
              }} 项
            </strong>
          </div>
          <div>
            <span>下次自动检查</span>
            <strong>{{ formatOptionalDateTime(selectedRun.nextWakeAt) }}</strong>
          </div>
          <div>
            <span>开放通知</span>
            <strong>{{ openNotificationCount(selectedRun) }} 项</strong>
          </div>
        </div>

        <div v-if="selectedRun.pendingInterrupt" class="run-human-task">
          <span><UiIcon name="alert" /></span>
          <div>
            <small>当前人工任务 · {{ humanTaskOwner(selectedRun) }}</small>
            <strong>{{ selectedRun.pendingInterrupt.title }}</strong>
            <p>{{ selectedRun.pendingInterrupt.description }}</p>
          </div>
          <button type="button" @click="openCase(selectedRun.caseNumber)">
            前往处理
            <UiIcon name="arrow" />
          </button>
        </div>

        <ShipmentAgentWorkstreams :run="selectedRun" />

        <details class="run-technical-details">
          <summary>
            <span><UiIcon name="server" />技术详情与审计轨迹</span>
            <small>默认收起，供开发、审计和故障排查使用</small>
          </summary>
          <div class="run-technical-facts">
            <div><span>Run ID</span><code>{{ selectedRun.runId }}</code></div>
            <div><span>当前节点代码</span><code>{{ selectedRun.currentStage }}</code></div>
            <div><span>工作流版本</span><code>{{ selectedRun.workflowVersion }}</code></div>
            <div><span>运行版本</span><code>R{{ selectedRun.runVersion }}</code></div>
          </div>
          <ShipmentAgentTrace :events="selectedRun.events" />
        </details>
      </section>
    </div>

    <details class="agent-inventory-disclosure">
      <summary>
        <span class="inventory-summary-icon"><UiIcon name="server" /></span>
        <div>
          <strong>自动化能力目录</strong>
          <small>查看发运协同、专业作业、连接器与后续能力边界</small>
        </div>
        <em>3 项已接入 · 1 项规划中</em>
        <UiIcon class="inventory-chevron" name="arrow" />
      </summary>

      <section class="agent-inventory" aria-label="自动化能力目录">
        <article class="inventory-card primary">
          <span class="inventory-icon"><UiIcon name="server" /></span>
          <div class="inventory-copy">
            <span>ORCHESTRATOR · LANGGRAPH</span>
            <h3>Shipment Operations Agent</h3>
            <p>围绕一票案件协调资料核验、装载、订舱、外部等待和人工结案。</p>
          </div>
          <div class="inventory-state">
            <i></i>
            已接入
          </div>
        </article>

        <article class="inventory-card">
          <span class="inventory-icon load"><UiIcon name="box" /></span>
          <div class="inventory-copy">
            <span>SPECIALIST AGENT · LANGGRAPH</span>
            <h3>智能装载 Agent</h3>
            <p>约束追问、确定性规划、3D 复核、审批、重规划和正式版本。</p>
          </div>
          <div class="inventory-state">
            <i></i>
            已接入
          </div>
        </article>

        <article class="inventory-card">
          <span class="inventory-icon booking"><UiIcon name="database" /></span>
          <div class="inventory-copy">
            <span>SKILL / CONNECTOR</span>
            <h3>LINK 七步订舱</h3>
            <p>确定性自动填单、幂等投递、承运人异步回调和人工确认。</p>
          </div>
          <div class="inventory-state skill">
            技能
          </div>
        </article>

        <article class="inventory-card planned">
          <span class="inventory-icon evidence"><UiIcon name="file" /></span>
          <div class="inventory-copy">
            <span>NEXT CAPABILITY</span>
            <h3>文档证据与合规 RAG</h3>
            <p>后续接入来源引用、冲突检查和法规证据；当前尚未伪装为已实现 Agent。</p>
          </div>
          <div class="inventory-state planned">
            规划中
          </div>
        </article>
      </section>
    </details>
  </section>
</template>

<style scoped>
.agent-center {
  display: grid;
  gap: 18px;
}

.agent-center-hero {
  display: flex;
  min-height: 112px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px;
  border: 1px solid #1f3930;
  border-radius: 16px;
  color: #f4faf7;
  background:
    radial-gradient(circle at 78% 14%, rgba(115, 202, 165, 0.2), transparent 28%),
    linear-gradient(135deg, #14221d, #1e3b31 68%, #285744);
  box-shadow: 0 18px 48px rgba(24, 56, 45, 0.16);
}

.agent-center-breadcrumb {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #88aa9d;
  font-size: 10px;
}

.agent-center-breadcrumb i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #68b293;
}

.agent-center-hero h2,
.agent-center-hero p {
  margin: 0;
}

.agent-center-hero h2 {
  margin-top: 7px;
  font-size: 24px;
  letter-spacing: -0.035em;
}

.agent-center-hero p {
  max-width: 670px;
  margin-top: 5px;
  color: #b6c9c1;
  font-size: 12px;
  line-height: 1.5;
}

.agent-center-actions {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.agent-center-actions > div {
  text-align: right;
}

.agent-center-actions span,
.agent-center-actions strong {
  display: block;
}

.agent-center-actions span {
  color: #88a79b;
  font-size: 10px;
}

.agent-center-actions strong {
  margin-top: 4px;
  color: #d6e7e0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
}

.agent-center-actions button {
  display: inline-flex;
  min-height: 37px;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid rgba(204, 228, 218, 0.2);
  border-radius: 9px;
  color: #e2eee9;
  background: rgba(255, 255, 255, 0.07);
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
}

.agent-center-actions .auto-refresh-toggle {
  color: #bfd3ca;
}

.agent-center-actions .auto-refresh-toggle i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #73877f;
}

.agent-center-actions .auto-refresh-toggle.active {
  border-color: rgba(140, 214, 182, 0.38);
  color: #e3f2ec;
  background: rgba(108, 190, 154, 0.14);
}

.agent-center-actions .auto-refresh-toggle.active i {
  background: #72c79f;
  box-shadow: 0 0 0 3px rgba(114, 199, 159, 0.13);
}

.agent-center-actions button:disabled {
  cursor: wait;
  opacity: 0.58;
}

.agent-center-actions :deep(.ui-icon) {
  width: 13px;
}

.agent-center-error {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 12px 14px;
  border: 1px solid #eccccc;
  border-radius: 11px;
  color: #8c4545;
  background: #fff7f7;
}

.agent-center-error > span {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  border-radius: 50%;
  background: #f8e4e4;
  font-weight: 800;
}

.agent-center-error > div {
  flex: 1;
}

.agent-center-error strong,
.agent-center-error p {
  display: block;
  margin: 0;
}

.agent-center-error strong {
  font-size: 10px;
}

.agent-center-error p {
  margin-top: 3px;
  font-size: 10px;
}

.agent-center-error button {
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  text-decoration: underline;
}

.agent-inventory-disclosure {
  overflow: hidden;
  border: 1px solid #dfe6e2;
  border-radius: 14px;
  background: #fff;
}

.agent-inventory-disclosure > summary {
  display: grid;
  min-height: 66px;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  gap: 11px;
  align-items: center;
  padding: 11px 15px;
  color: #35423d;
  cursor: pointer;
  list-style: none;
}

.agent-inventory-disclosure > summary::-webkit-details-marker {
  display: none;
}

.inventory-summary-icon {
  display: grid;
  width: 35px;
  height: 35px;
  place-items: center;
  border-radius: 10px;
  color: #47705f;
  background: #e7f0ec;
}

.inventory-summary-icon :deep(.ui-icon) {
  width: 17px;
}

.agent-inventory-disclosure summary strong,
.agent-inventory-disclosure summary small {
  display: block;
}

.agent-inventory-disclosure summary strong {
  font-size: 12px;
}

.agent-inventory-disclosure summary small {
  margin-top: 3px;
  color: #8b9691;
  font-size: 10px;
}

.agent-inventory-disclosure summary em {
  color: #6f7c76;
  font-size: 10px;
  font-style: normal;
}

.inventory-chevron {
  width: 14px;
  color: #8d9793;
  transition: transform 0.18s ease;
}

.agent-inventory-disclosure[open] .inventory-chevron {
  transform: rotate(90deg);
}

.agent-inventory {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 11px;
  padding: 0 11px 11px;
}

.inventory-card {
  position: relative;
  display: grid;
  min-width: 0;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  padding: 14px;
  border: 1px solid #e0e6e3;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(27, 52, 44, 0.04);
}

.inventory-card.primary {
  border-color: #a8caba;
  background:
    linear-gradient(145deg, #f3f9f6, #fff);
}

.inventory-card.planned {
  border-style: dashed;
  background: #fafbfb;
}

.inventory-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 11px;
  color: #fff;
  background: #356a58;
}

.inventory-icon.load {
  color: #486e91;
  background: #e5eef7;
}

.inventory-icon.booking {
  color: #775b8f;
  background: #eee8f5;
}

.inventory-icon.evidence {
  color: #8d6b36;
  background: #f5ecd9;
}

.inventory-icon :deep(.ui-icon) {
  width: 17px;
}

.inventory-copy {
  min-width: 0;
}

.inventory-copy > span {
  color: #8b9691;
  font-size: 10px;
  font-weight: 760;
  letter-spacing: 0.12em;
}

.inventory-copy h3,
.inventory-copy p {
  margin: 0;
}

.inventory-copy h3 {
  overflow: hidden;
  margin-top: 5px;
  color: #2b3732;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inventory-copy p {
  margin-top: 7px;
  color: #79847f;
  font-size: 10px;
  line-height: 1.55;
}

.inventory-state {
  position: absolute;
  top: 9px;
  right: 9px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 6px;
  border-radius: 999px;
  color: #3f725c;
  background: #e6f2eb;
  font-size: 10px;
}

.inventory-state i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #5ca27e;
}

.inventory-state.skill {
  color: #6e5780;
  background: #eee8f3;
}

.inventory-state.planned {
  color: #7b7f7d;
  background: #eff1f0;
}

.agent-center-kpis {
  display: grid;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  gap: 11px;
}

.agent-center-kpis button {
  display: grid;
  grid-column: span 3;
  min-height: 86px;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  align-items: center;
  padding: 12px;
  border: 1px solid #e1e6e4;
  border-radius: 13px;
  color: inherit;
  background: #fff;
  cursor: pointer;
  text-align: left;
}

.agent-center-kpis button:nth-child(n + 5) {
  grid-column: span 4;
}

.agent-center-kpis button.active {
  border-color: #9fbfaf;
  box-shadow: 0 0 0 3px rgba(63, 117, 94, 0.07);
}

.agent-center-kpis button > span {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 10px;
  color: #47705f;
  background: #e6f0eb;
}

.agent-center-kpis button > span.processing {
  color: #47705f;
  background: #e6f0eb;
}

.agent-center-kpis button > span.human {
  color: #876923;
  background: #f6ecd2;
}

.agent-center-kpis button > span.resource {
  color: #6f5b8d;
  background: #eee9f6;
}

.agent-center-kpis button > span.external {
  color: #4d6e91;
  background: #e7eff8;
}

.agent-center-kpis button > span.failure {
  color: #965050;
  background: #f8e9e9;
}

.agent-center-kpis button > span.completed {
  color: #3f775a;
  background: #e4f1e9;
}

.agent-center-kpis button > span :deep(.ui-icon) {
  width: 17px;
}

.agent-center-kpis small,
.agent-center-kpis strong,
.agent-center-kpis em {
  display: block;
  font-style: normal;
}

.agent-center-kpis small {
  color: #808a86;
  font-size: 11px;
}

.agent-center-kpis strong {
  margin-top: 4px;
  color: #28352f;
  font-size: 20px;
}

.agent-center-kpis em {
  margin-top: 3px;
  color: #9aa29f;
  font-size: 10px;
}

.agent-center-toolbar {
  display: flex;
  min-height: 42px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.agent-center-toolbar label {
  display: flex;
  width: min(390px, 100%);
  min-height: 38px;
  align-items: center;
  gap: 8px;
  padding: 0 11px;
  border: 1px solid #dce2df;
  border-radius: 9px;
  color: #909995;
  background: #fff;
}

.agent-center-toolbar label :deep(.ui-icon) {
  width: 14px;
}

.agent-center-toolbar input {
  width: 100%;
  border: 0;
  outline: 0;
  color: #35413c;
  background: transparent;
  font-size: 11px;
}

.agent-center-toolbar > span {
  color: #8b9591;
  font-size: 10px;
}

.agent-center-loading,
.agent-center-empty {
  display: grid;
  min-height: 260px;
  place-items: center;
  align-content: center;
  gap: 8px;
  border: 1px solid #e1e6e4;
  border-radius: 15px;
  color: #8d9793;
  background: #fff;
}

.agent-center-loading {
  grid-template-columns: auto auto;
}

.agent-center-loading > span {
  width: 27px;
  height: 27px;
  border: 3px solid #e2e8e5;
  border-top-color: #4c7d69;
  border-radius: 50%;
  animation: center-spin 0.8s linear infinite;
}

.agent-center-loading strong,
.agent-center-loading p,
.agent-center-empty strong,
.agent-center-empty p {
  display: block;
  margin: 0;
}

.agent-center-loading strong,
.agent-center-empty strong {
  color: #596560;
  font-size: 11px;
}

.agent-center-loading p,
.agent-center-empty p {
  margin-top: 4px;
  font-size: 10px;
}

.agent-center-empty > :deep(.ui-icon) {
  width: 28px;
}

.agent-center-empty a {
  margin-top: 8px;
  color: #376b59;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
}

.agent-center-grid {
  display: grid;
  grid-template-columns: minmax(280px, 0.72fr) minmax(0, 1.55fr);
  gap: 13px;
  align-items: start;
}

.agent-center-grid.single-panel {
  grid-template-columns: 1fr;
}

.run-list-panel,
.run-inspector {
  min-width: 0;
  border: 1px solid #e1e6e4;
  border-radius: 15px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(27, 52, 44, 0.04);
}

.run-list-heading,
.run-inspector-heading {
  display: flex;
  min-height: 66px;
  align-items: center;
  justify-content: space-between;
  gap: 13px;
  padding: 14px 16px;
  border-bottom: 1px solid #edf0ef;
}

.run-list-heading > div > span,
.run-inspector-heading > div > span {
  color: #8e9894;
  font-size: 10px;
  font-weight: 760;
  letter-spacing: 0.13em;
}

.run-list-heading h3,
.run-inspector-heading h3 {
  margin: 4px 0 0;
  color: #2e3a35;
  font-size: 13px;
}

.run-list-heading > span {
  color: #969f9b;
  font-size: 10px;
}

.run-list {
  max-height: 720px;
  overflow-y: auto;
  padding: 6px;
}

.run-list > button {
  display: grid;
  width: 100%;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  padding: 11px 9px;
  border: 1px solid transparent;
  border-bottom-color: #eef1f0;
  border-radius: 9px;
  color: inherit;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.run-list > button:hover,
.run-list > button.selected {
  border-color: #d9e6df;
  background: #f4f9f6;
}

.run-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #84918b;
  box-shadow: 0 0 0 3px #edf0ef;
}

.run-status-dot[data-status="WAITING_HUMAN"] {
  background: #d29d3e;
  box-shadow: 0 0 0 3px #f8efd9;
}

.run-status-dot[data-status="WAITING_RESOURCE"] {
  background: #639379;
  box-shadow: 0 0 0 3px #e6f0eb;
}

.run-status-dot[data-status="WAITING_EXTERNAL"] {
  background: #658db8;
  box-shadow: 0 0 0 3px #e7eff8;
}

.run-status-dot[data-status="COMPLETED"] {
  background: #4c9b73;
  box-shadow: 0 0 0 3px #e4f1e9;
}

.run-status-dot[data-status="FAILED"] {
  background: #be6a6a;
  box-shadow: 0 0 0 3px #f6e7e7;
}

.run-status-dot[data-status="CANCELLED"] {
  background: #89928e;
  box-shadow: 0 0 0 3px #edf0ef;
}

.run-list-copy {
  min-width: 0;
}

.run-list-copy span,
.run-list-copy strong,
.run-list-copy small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-list-copy span {
  color: #53655d;
  font-size: 12px;
  font-weight: 700;
}

.run-list-copy strong {
  margin-top: 4px;
  color: #33413b;
  font-size: 13px;
}

.run-list-copy small {
  margin-top: 4px;
  color: #9aa29f;
  font-size: 11px;
}

.run-list-state {
  text-align: right;
}

.run-list-state strong,
.run-list-state time {
  display: block;
  white-space: nowrap;
}

.run-list-state strong {
  color: #5e6d67;
  font-size: 12px;
}

.run-list-state time {
  margin-top: 4px;
  color: #9ca4a1;
  font-size: 11px;
}

.run-filter-empty {
  padding: 50px 16px;
  color: #929b98;
  font-size: 11px;
  text-align: center;
}

.run-inspector {
  display: grid;
  gap: 15px;
  padding-bottom: 17px;
}

.run-inspector-heading {
  padding: 15px 17px;
}

.run-inspector-heading small {
  display: block;
  margin-top: 4px;
  color: #929c98;
  font-size: 11px;
}

.run-inspector-heading button {
  display: inline-flex;
  min-height: 33px;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 1px solid #d8e3dd;
  border-radius: 8px;
  color: #3d6c5b;
  background: #f4f9f6;
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
}

.run-inspector-heading button :deep(.ui-icon) {
  width: 12px;
}

.run-inspector-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 0 17px;
  overflow: hidden;
  border: 1px solid #e4e9e7;
  border-radius: 10px;
  background: #e4e9e7;
}

.run-inspector-facts > div {
  min-width: 0;
  padding: 10px;
  background: #fbfcfc;
}

.run-inspector-facts span,
.run-inspector-facts strong {
  display: block;
}

.run-inspector-facts span {
  color: #939c98;
  font-size: 11px;
}

.run-inspector-facts strong {
  overflow: hidden;
  margin-top: 4px;
  color: #35423d;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-human-task {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: start;
  margin: 0 17px;
  padding: 12px;
  border: 1px solid #ead6ad;
  border-radius: 10px;
  background: #fff9eb;
}

.run-human-task > span {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 8px;
  color: #8d681f;
  background: #f4e4bd;
}

.run-human-task > span :deep(.ui-icon) {
  width: 14px;
}

.run-human-task small,
.run-human-task strong,
.run-human-task p {
  display: block;
  margin: 0;
}

.run-human-task small {
  color: #a17d38;
  font-size: 11px;
}

.run-human-task strong {
  margin-top: 4px;
  color: #564a34;
  font-size: 13px;
}

.run-human-task p {
  margin-top: 4px;
  color: #80725a;
  font-size: 12px;
  line-height: 1.5;
}

.run-human-task button {
  display: inline-flex;
  min-height: 34px;
  align-items: center;
  align-self: center;
  gap: 6px;
  padding: 0 11px;
  border: 1px solid #d5b978;
  border-radius: 8px;
  color: #745518;
  background: #fffdf8;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.run-human-task button :deep(.ui-icon) {
  width: 12px;
}

.run-inspector > :deep(.agent-workstreams) {
  margin: 0 17px;
  padding: 14px;
  border: 1px solid #e4e9e7;
  border-radius: 12px;
}

.run-technical-details {
  margin: 0 17px;
  overflow: hidden;
  border: 1px solid #e4e9e7;
  border-radius: 12px;
  background: #fbfcfc;
}

.run-technical-details > summary {
  display: flex;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px;
  cursor: pointer;
  list-style: none;
}

.run-technical-details > summary::-webkit-details-marker {
  display: none;
}

.run-technical-details > summary span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #4b5b55;
  font-size: 12px;
  font-weight: 700;
}

.run-technical-details > summary span :deep(.ui-icon) {
  width: 14px;
}

.run-technical-details > summary small {
  color: #929c98;
  font-size: 11px;
}

.run-technical-details[open] > summary {
  border-bottom: 1px solid #e8ecea;
}

.run-technical-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 14px;
  overflow: hidden;
  border: 1px solid #e7ebe9;
  border-radius: 9px;
  background: #e7ebe9;
}

.run-technical-facts > div {
  min-width: 0;
  padding: 10px;
  background: #fff;
}

.run-technical-facts span,
.run-technical-facts code {
  display: block;
}

.run-technical-facts span {
  color: #919b97;
  font-size: 10px;
}

.run-technical-facts code {
  overflow: hidden;
  margin-top: 5px;
  color: #53625c;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-technical-details > :deep(.shipment-trace) {
  margin: 0 14px 14px;
  padding: 14px;
  border: 1px solid #e4e9e7;
  border-radius: 10px;
  background: #fff;
}

@keyframes center-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1080px) {
  .agent-inventory {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .agent-center-grid {
    grid-template-columns: 1fr;
  }

  .run-list {
    max-height: 380px;
  }
}

@media (max-width: 720px) {
  .agent-center-hero {
    display: grid;
    align-items: start;
  }

  .agent-center-actions {
    display: grid;
    width: 100%;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: center;
    justify-content: start;
  }

  .agent-center-actions > div {
    grid-column: 1 / -1;
    text-align: left;
  }

  .agent-center-actions button {
    min-width: 0;
    justify-content: center;
  }

  .agent-inventory,
  .agent-center-kpis {
    grid-template-columns: 1fr;
  }

  .agent-center-kpis button,
  .agent-center-kpis button:nth-child(n + 5) {
    grid-column: auto;
  }

  .agent-center-toolbar {
    display: grid;
  }

  .run-inspector-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
