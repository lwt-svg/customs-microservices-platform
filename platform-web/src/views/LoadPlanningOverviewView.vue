<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  getActiveLoadPlanningAgentRuns,
  getLoadPlanningAgentRunHistory,
  getLoadPlanningTasks,
  getLoadPlanVersions,
  type LoadPlanVersion,
  type LoadPlanningAgentRun,
  type LoadPlanningAgentStatus,
  type LoadPlanningTaskSummary,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

interface ControlTowerTask {
  task: LoadPlanningTaskSummary
  activeRuns: LoadPlanningAgentRun[]
  runHistory: LoadPlanningAgentRun[]
  versions: LoadPlanVersion[]
  runError: string
  historyError: string
  versionError: string
}

interface WorkQueueItem {
  taskNumber: string
  taskName: string
  type: 'INPUT' | 'REVIEW' | 'RELEASE' | 'FAILED' | 'REPLAN' | 'START'
  title: string
  detail: string
  priority: number
  updatedAt: string
}

const taskRows = ref<ControlTowerTask[]>([])
const loading = ref(true)
const loadError = ref('')
const lastRefreshedAt = ref<Date | null>(null)

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '读取状态失败'
}

/**
 * 控制塔先读取任务清单，再并行补齐每个任务的“可恢复运行、完整历史、正式版本”。
 *
 * 两类状态相互独立：单个接口失败时仍保留已经成功读取的业务数据，并在对应位置显示
 * “状态未知”，不能把请求失败误判成“没有待办”或“尚未发布”。
 */
async function loadControlTower(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    const tasks = await getLoadPlanningTasks()
    const rows = await Promise.all(
      tasks.map(async (task): Promise<ControlTowerTask> => {
        const [runResult, historyResult, versionResult] = await Promise.allSettled([
          getActiveLoadPlanningAgentRuns(task.taskNumber),
          getLoadPlanningAgentRunHistory(task.taskNumber),
          getLoadPlanVersions(task.taskNumber),
        ])

        return {
          task,
          activeRuns: runResult.status === 'fulfilled' ? runResult.value : [],
          runHistory:
            historyResult.status === 'fulfilled' ? historyResult.value : [],
          versions: versionResult.status === 'fulfilled' ? versionResult.value : [],
          runError: runResult.status === 'rejected' ? errorMessage(runResult.reason) : '',
          historyError:
            historyResult.status === 'rejected' ? errorMessage(historyResult.reason) : '',
          versionError:
            versionResult.status === 'rejected' ? errorMessage(versionResult.reason) : '',
        }
      }),
    )

    taskRows.value = rows
    lastRefreshedAt.value = new Date()
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

function hasRunStatus(row: ControlTowerTask, status: LoadPlanningAgentStatus): boolean {
  return row.activeRuns.some((run) => run.status === status)
}

function latestVersion(row: ControlTowerTask): LoadPlanVersion | null {
  return row.versions.reduce<LoadPlanVersion | null>(
    (latest, version) =>
      !latest || version.versionNumber > latest.versionNumber ? version : latest,
    null,
  )
}

function latestRun(row: ControlTowerTask): LoadPlanningAgentRun | null {
  return row.runHistory[0] ?? null
}

function hasFormalVersionForRun(
  row: ControlTowerTask,
  run: LoadPlanningAgentRun,
): boolean {
  return row.versions.some((version) => version.sourceRunId === run.runId)
}

const taskCount = computed(() => taskRows.value.length)
const totalBoxCount = computed(() =>
  taskRows.value.reduce((total, row) => total + row.task.cargoBoxCount, 0),
)
const awaitingInputCount = computed(
  () => taskRows.value.filter((row) => hasRunStatus(row, 'AWAITING_INPUT')).length,
)
const awaitingReviewCount = computed(
  () => taskRows.value.filter((row) => hasRunStatus(row, 'READY_FOR_REVIEW')).length,
)
const awaitingReleaseCount = computed(
  () =>
    taskRows.value.filter((row) => {
      const run = latestRun(row)
      return (
        run?.status === 'APPROVED' &&
        !row.versionError &&
        !hasFormalVersionForRun(row, run)
      )
    }).length,
)
const lockedTaskCount = computed(
  () => taskRows.value.filter((row) => row.versions.length > 0).length,
)
const runFailureCount = computed(
  () => taskRows.value.filter((row) => row.runError || row.historyError).length,
)
const versionFailureCount = computed(
  () => taskRows.value.filter((row) => row.versionError).length,
)
const partialFailureCount = computed(
  () =>
    taskRows.value.filter(
      (row) => row.runError || row.historyError || row.versionError,
    ).length,
)

const workQueue = computed<WorkQueueItem[]>(() => {
  const items: WorkQueueItem[] = []

  for (const row of taskRows.value) {
    if (row.runError || row.historyError || row.versionError) continue

    if (hasRunStatus(row, 'AWAITING_INPUT')) {
      const run = row.activeRuns.find((candidate) => candidate.status === 'AWAITING_INPUT')
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'INPUT',
        title: '补充装载约束',
        detail: `${run?.questions.length ?? 0} 项必要信息等待业务确认`,
        priority: 1,
        updatedAt: row.task.updatedAt,
      })
      continue
    }

    if (hasRunStatus(row, 'READY_FOR_REVIEW')) {
      const run = row.activeRuns.find((candidate) => candidate.status === 'READY_FOR_REVIEW')
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'REVIEW',
        title: '审批候选方案',
        detail: `${run?.plans.length ?? 0} 套方案已生成，等待人工复核`,
        priority: 2,
        updatedAt: row.task.updatedAt,
      })
      continue
    }

    const recentRun = latestRun(row)
    if (
      recentRun?.status === 'APPROVED' &&
      !hasFormalVersionForRun(row, recentRun)
    ) {
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'RELEASE',
        title: '发布正式版本',
        detail: `候选方案 ${recentRun.selectedPlanCode ?? ''} 已批准，等待锁定为现场执行版本`,
        priority: 3,
        updatedAt: row.task.updatedAt,
      })
      continue
    }

    if (recentRun?.status === 'FAILED') {
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'FAILED',
        title: '处理规划异常',
        detail: recentRun.failureMessage || '规划运行失败，需要检查后重新规划',
        priority: 4,
        updatedAt: row.task.updatedAt,
      })
      continue
    }

    if (recentRun?.status === 'REJECTED') {
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'REPLAN',
        title: '重新规划装载方案',
        detail: '上一批次已驳回，可沿用已确认约束发起新的独立试算',
        priority: 4,
        updatedAt: row.task.updatedAt,
      })
      continue
    }

    if (row.runHistory.length === 0 && row.versions.length === 0) {
      items.push({
        taskNumber: row.task.taskNumber,
        taskName: row.task.taskName,
        type: 'START',
        title: '启动装载规划',
        detail: `${row.task.cargoBoxCount} 箱货物尚无正式装载版本`,
        priority: 5,
        updatedAt: row.task.updatedAt,
      })
    }
  }

  return items.sort(
    (left, right) =>
      left.priority - right.priority ||
      new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  )
})

const recentlyLockedTasks = computed(() =>
  taskRows.value
    .map((row) => ({ row, version: latestVersion(row) }))
    .filter(
      (
        item,
      ): item is {
        row: ControlTowerTask
        version: LoadPlanVersion
      } => item.version !== null,
    )
    .sort(
      (left, right) =>
        new Date(right.version.lockedAt).getTime() -
        new Date(left.version.lockedAt).getTime(),
    ),
)

function taskSituation(row: ControlTowerTask): {
  label: string
  tone: 'warning' | 'review' | 'success' | 'neutral' | 'danger'
  detail: string
} {
  if (row.runError || row.historyError) {
    return { label: '状态未知', tone: 'danger', detail: '规划运行状态读取失败' }
  }
  if (hasRunStatus(row, 'AWAITING_INPUT')) {
    return { label: '待补资料', tone: 'warning', detail: '流程已暂停' }
  }
  if (hasRunStatus(row, 'READY_FOR_REVIEW')) {
    return { label: '待审批', tone: 'review', detail: '候选方案已生成' }
  }
  const recentRun = latestRun(row)
  if (
    recentRun?.status === 'APPROVED' &&
    !row.versionError &&
    !hasFormalVersionForRun(row, recentRun)
  ) {
    return { label: '待发布', tone: 'review', detail: '候选方案已批准' }
  }
  if (recentRun?.status === 'FAILED') {
    return { label: '运行异常', tone: 'danger', detail: '需要检查并重规划' }
  }
  if (recentRun?.status === 'REJECTED') {
    return { label: '待重新规划', tone: 'danger', detail: '上一方案已驳回' }
  }
  if (row.activeRuns.length > 0) {
    return { label: '规划中', tone: 'review', detail: '运行状态已保存' }
  }
  if (row.versionError) {
    return { label: '状态待确认', tone: 'danger', detail: '正式版本状态读取失败' }
  }
  if (row.versions.length > 0) {
    return { label: '已发布', tone: 'success', detail: '当前无待处理运行' }
  }
  return { label: '待规划', tone: 'neutral', detail: '尚未启动规划' }
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
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
  }).format(value)
}

function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    DRAFT: '草稿',
    ACTIVE: '进行中',
    COMPLETED: '已完成',
    CANCELLED: '已取消',
  }
  return labels[status] ?? status
}

function queueTypeLabel(type: WorkQueueItem['type']): string {
  const labels: Record<WorkQueueItem['type'], string> = {
    INPUT: '资料',
    REVIEW: '审批',
    RELEASE: '发布',
    FAILED: '异常',
    REPLAN: '重算',
    START: '规划',
  }
  return labels[type]
}

onMounted(loadControlTower)
</script>

<template>
  <section class="load-control-tower">
    <header class="tower-header">
      <div class="tower-heading">
        <div class="tower-breadcrumb">
          <span>供应链运营</span>
          <i></i>
          <strong>智能装载</strong>
        </div>
        <h2>智能装载控制塔</h2>
        <p>集中跟踪装载任务、约束补充、方案审批与正式版本发布状态。</p>
      </div>

      <div class="tower-actions">
        <div class="sync-state">
          <span>数据同步时间</span>
          <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="loadControlTower">
          <UiIcon name="refresh" />
          {{ loading ? '同步中' : '刷新数据' }}
        </button>
        <RouterLink class="enter-button" to="/supply-chain/load-planning/tasks">
          进入任务中心
          <UiIcon name="arrow" />
        </RouterLink>
      </div>
    </header>

    <div v-if="loadError" class="state-banner error" role="alert">
      <span class="banner-icon">!</span>
      <div>
        <strong>控制塔数据加载失败</strong>
        <p>{{ loadError }}</p>
      </div>
      <button type="button" @click="loadControlTower">重新加载</button>
    </div>

    <div v-if="partialFailureCount > 0" class="state-banner warning" role="status">
      <span class="banner-icon"><UiIcon name="alert" /></span>
      <div>
        <strong>{{ partialFailureCount }} 个任务的运行历史或正式版本状态未能完整同步</strong>
        <p>相关位置已标记为“状态未知”，受影响的指标显示为“—”。</p>
      </div>
      <button type="button" @click="loadControlTower">重新同步</button>
    </div>

    <div v-if="loading && taskRows.length === 0" class="tower-loading" aria-live="polite">
      <span class="loading-spinner"></span>
      <div>
        <strong>正在汇总装载业务状态</strong>
        <p>正在读取任务、规划运行历史和正式版本。</p>
      </div>
    </div>

    <template v-else-if="taskRows.length > 0">
      <section class="kpi-grid" aria-label="装载业务关键指标">
        <article class="kpi-card">
          <span class="kpi-icon task"><UiIcon name="layers" /></span>
          <div>
            <span>任务总数</span>
            <strong>{{ taskCount }}</strong>
            <small>当前纳管装载任务</small>
          </div>
        </article>
        <article class="kpi-card">
          <span class="kpi-icon boxes"><UiIcon name="box" /></span>
          <div>
            <span>货物总箱数</span>
            <strong>{{ totalBoxCount.toLocaleString('zh-CN') }}</strong>
            <small>来自任务主数据</small>
          </div>
        </article>
        <article class="kpi-card attention">
          <span class="kpi-icon input"><UiIcon name="file" /></span>
          <div>
            <span>待补资料</span>
            <strong>{{ runFailureCount > 0 ? '—' : awaitingInputCount }}</strong>
            <small>运行已暂停等待约束</small>
          </div>
        </article>
        <article class="kpi-card attention">
          <span class="kpi-icon review"><UiIcon name="check" /></span>
          <div>
            <span>待审批</span>
            <strong>{{ runFailureCount > 0 ? '—' : awaitingReviewCount }}</strong>
            <small>候选方案待人工确认</small>
          </div>
        </article>
        <article class="kpi-card attention">
          <span class="kpi-icon release"><UiIcon name="upload" /></span>
          <div>
            <span>待正式发布</span>
            <strong>
              {{ runFailureCount > 0 || versionFailureCount > 0 ? '—' : awaitingReleaseCount }}
            </strong>
            <small>已批准候选等待锁定</small>
          </div>
        </article>
        <article class="kpi-card">
          <span class="kpi-icon locked"><UiIcon name="shield" /></span>
          <div>
            <span>已锁定任务</span>
            <strong>{{ versionFailureCount > 0 ? '—' : lockedTaskCount }}</strong>
            <small>已有不可变正式版本</small>
          </div>
        </article>
      </section>

      <div class="tower-columns">
        <section class="tower-panel work-queue-panel">
          <div class="section-heading">
            <div>
              <span class="section-kicker">WORK QUEUE</span>
              <h3>业务待办</h3>
              <p>按补资料、审批、正式发布、异常和待规划顺序排列</p>
            </div>
            <span class="section-count">
              {{ partialFailureCount > 0 ? `${workQueue.length} 项已知` : `${workQueue.length} 项` }}
            </span>
          </div>

          <div v-if="workQueue.length > 0" class="work-queue">
            <article
              v-for="item in workQueue.slice(0, 6)"
              :key="`${item.taskNumber}-${item.type}`"
              class="queue-item"
            >
              <span class="queue-type" :class="item.type.toLowerCase()">
                {{ queueTypeLabel(item.type) }}
              </span>
              <div class="queue-copy">
                <div>
                  <strong>{{ item.title }}</strong>
                  <span>{{ item.taskNumber }}</span>
                </div>
                <h4>{{ item.taskName }}</h4>
                <p>{{ item.detail }}</p>
              </div>
              <RouterLink
                class="row-link"
                to="/supply-chain/load-planning/tasks"
                :aria-label="`处理 ${item.taskName}`"
              >
                <UiIcon name="arrow" />
              </RouterLink>
            </article>
          </div>

          <div v-else-if="partialFailureCount > 0" class="panel-empty">
            <span><UiIcon name="alert" /></span>
            <div>
              <strong>待办状态未完整同步</strong>
              <p>当前没有已确认待办，但仍有任务状态读取失败，请重新同步后再判断。</p>
            </div>
          </div>

          <div v-else class="panel-empty">
            <span><UiIcon name="check" /></span>
            <div>
              <strong>当前没有业务待办</strong>
              <p>所有已读取任务均无待补资料、待审批或待启动事项。</p>
            </div>
          </div>

          <RouterLink
            v-if="workQueue.length > 6"
            class="panel-footer-link"
            to="/supply-chain/load-planning/tasks"
          >
            查看全部 {{ workQueue.length }} 项待办
            <UiIcon name="arrow" />
          </RouterLink>
        </section>

        <section class="tower-panel version-panel">
          <div class="section-heading">
            <div>
              <span class="section-kicker">RELEASED PLANS</span>
              <h3>正式版本</h3>
              <p>仅展示后端已锁定的不可变版本</p>
            </div>
            <span class="section-count">
              {{
                versionFailureCount > 0
                  ? `${lockedTaskCount} 个已知`
                  : `${lockedTaskCount} 个任务`
              }}
            </span>
          </div>

          <div v-if="recentlyLockedTasks.length > 0" class="version-list">
            <article
              v-for="{ row, version } in recentlyLockedTasks.slice(0, 4)"
              :key="version.versionId"
              class="version-item"
            >
              <div class="version-title">
                <span>V{{ version.versionNumber }}</span>
                <div>
                  <strong>{{ row.task.taskName }}</strong>
                  <small>{{ row.task.taskNumber }}</small>
                </div>
                <em>已锁定</em>
              </div>
              <dl>
                <div>
                  <dt>方案</dt>
                  <dd>{{ version.planCode }}</dd>
                </div>
                <div>
                  <dt>托盘</dt>
                  <dd>{{ version.selectedPalletCode }}</dd>
                </div>
                <div>
                  <dt>发布时间</dt>
                  <dd>{{ formatDate(version.lockedAt) }}</dd>
                </div>
              </dl>
            </article>
          </div>

          <div v-else-if="versionFailureCount > 0" class="panel-empty compact">
            <span><UiIcon name="alert" /></span>
            <div>
              <strong>正式版本状态待同步</strong>
              <p>当前未读取到已锁定版本，但部分版本接口失败，不能判定为“暂无版本”。</p>
            </div>
          </div>

          <div v-else class="panel-empty compact">
            <span><UiIcon name="shield" /></span>
            <div>
              <strong>暂无正式装载版本</strong>
              <p>候选方案经人工审批并发布后，将在这里形成版本记录。</p>
            </div>
          </div>
        </section>
      </div>

      <section class="tower-panel task-situation-panel">
        <div class="section-heading table-heading">
          <div>
            <span class="section-kicker">TASK SITUATION</span>
            <h3>任务态势</h3>
            <p>任务规模、规划进度与正式版本的统一视图</p>
          </div>
          <RouterLink class="text-link" to="/supply-chain/load-planning/tasks">
            管理装载任务
            <UiIcon name="arrow" />
          </RouterLink>
        </div>

        <div class="task-table-wrap">
          <table class="task-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>业务状态</th>
                <th>货物规模</th>
                <th>规划态势</th>
                <th>正式版本</th>
                <th>更新时间</th>
                <th><span class="visually-hidden">操作</span></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in taskRows" :key="row.task.id">
                <td>
                  <div class="task-identity">
                    <strong>{{ row.task.taskName }}</strong>
                    <span>{{ row.task.taskNumber }}</span>
                  </div>
                </td>
                <td>
                  <span class="business-status">{{ taskStatusLabel(row.task.status) }}</span>
                </td>
                <td>
                  <div class="scale-cell">
                    <strong>{{ row.task.cargoBoxCount }} 箱</strong>
                    <span>
                      {{ row.task.cargoItemCount }} 个货物项 ·
                      {{ row.task.palletSpecCount }} 个托盘规格
                    </span>
                  </div>
                </td>
                <td>
                  <div class="situation-cell">
                    <span class="situation-pill" :class="taskSituation(row).tone">
                      <i></i>
                      {{ taskSituation(row).label }}
                    </span>
                    <small>{{ taskSituation(row).detail }}</small>
                  </div>
                </td>
                <td>
                  <div v-if="row.versionError" class="version-cell unknown">
                    <strong>状态未知</strong>
                    <span>版本读取失败</span>
                  </div>
                  <div v-else-if="latestVersion(row)" class="version-cell locked">
                    <strong>V{{ latestVersion(row)?.versionNumber }}</strong>
                    <span>{{ latestVersion(row)?.planCode }}</span>
                  </div>
                  <div v-else class="version-cell">
                    <strong>未发布</strong>
                    <span>暂无正式版本</span>
                  </div>
                </td>
                <td class="date-cell">{{ formatDate(row.task.updatedAt) }}</td>
                <td>
                  <RouterLink
                    class="row-link"
                    to="/supply-chain/load-planning/tasks"
                    :aria-label="`查看 ${row.task.taskName}`"
                  >
                    <UiIcon name="arrow" />
                  </RouterLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <section v-else-if="!loadError" class="empty-control-tower">
      <span class="empty-illustration"><UiIcon name="box" /></span>
      <div>
        <span class="section-kicker">NO LOAD TASKS</span>
        <h3>还没有装载任务</h3>
        <p>先创建任务并录入已确认的箱规、数量和托盘约束，控制塔会自动汇总后续状态。</p>
      </div>
      <RouterLink class="enter-button" to="/supply-chain/load-planning/tasks">
        创建装载任务
        <UiIcon name="arrow" />
      </RouterLink>
    </section>
  </section>
</template>

<style scoped>
.load-control-tower {
  display: grid;
  gap: 16px;
  color: #24312c;
}

.tower-header {
  display: flex;
  min-height: 152px;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  padding: 28px 30px;
  overflow: hidden;
  border: 1px solid #dfe8e3;
  border-radius: 16px;
  background:
    radial-gradient(circle at 92% -15%, rgba(74, 139, 113, 0.15), transparent 39%),
    linear-gradient(135deg, #fbfdfc 0%, #f4f8f6 100%);
}

.tower-heading {
  min-width: 0;
}

.tower-breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #7a8a82;
  font-size: 11px;
}

.tower-breadcrumb i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #9eb0a7;
}

.tower-breadcrumb strong {
  color: #376755;
  font-weight: 650;
}

.tower-heading h2 {
  margin: 13px 0 7px;
  color: #19392f;
  font-size: clamp(24px, 2.7vw, 32px);
  font-weight: 680;
  letter-spacing: -0.035em;
}

.tower-heading p {
  margin: 0;
  color: #65766e;
  font-size: 12px;
  line-height: 1.7;
}

.tower-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 9px;
}

.sync-state {
  display: grid;
  min-width: 172px;
  gap: 4px;
  padding-right: 17px;
  border-right: 1px solid #dfe7e3;
  text-align: right;
}

.sync-state span {
  color: #85938d;
  font-size: 10px;
}

.sync-state strong {
  color: #4c5c55;
  font-size: 11px;
  font-weight: 600;
}

.refresh-button,
.enter-button {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 14px;
  border-radius: 9px;
  cursor: pointer;
  font-size: 11px;
  font-weight: 650;
  text-decoration: none;
}

.refresh-button {
  border: 1px solid #d6e0db;
  color: #4f6259;
  background: rgba(255, 255, 255, 0.86);
}

.refresh-button:hover:not(:disabled) {
  border-color: #bccdc5;
  background: #ffffff;
}

.refresh-button:disabled {
  cursor: wait;
  opacity: 0.58;
}

.enter-button {
  border: 1px solid #346a57;
  color: #ffffff;
  background: #346a57;
}

.enter-button:hover {
  background: #295846;
}

.refresh-button .ui-icon,
.enter-button .ui-icon {
  width: 15px;
  height: 15px;
}

.state-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid;
  border-radius: 11px;
}

.state-banner.error {
  border-color: #ecd1d1;
  color: #844747;
  background: #fff8f8;
}

.state-banner.warning {
  border-color: #eadfbd;
  color: #776025;
  background: #fffbef;
}

.banner-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.7);
  font-weight: 750;
}

.banner-icon .ui-icon {
  width: 15px;
}

.state-banner > div {
  display: grid;
  flex: 1;
  gap: 3px;
}

.state-banner strong {
  font-size: 11px;
}

.state-banner p {
  margin: 0;
  font-size: 10px;
  line-height: 1.5;
}

.state-banner button {
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
}

.tower-loading {
  display: flex;
  min-height: 310px;
  align-items: center;
  justify-content: center;
  gap: 14px;
  border: 1px solid #e1e7e4;
  border-radius: 14px;
  background: #ffffff;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #dfe8e3;
  border-top-color: #3f765f;
  border-radius: 50%;
  animation: spin 800ms linear infinite;
}

.tower-loading div {
  display: grid;
  gap: 5px;
}

.tower-loading strong {
  font-size: 12px;
}

.tower-loading p {
  margin: 0;
  color: #7b8982;
  font-size: 10px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 11px;
}

.kpi-card {
  display: flex;
  min-width: 0;
  min-height: 116px;
  align-items: flex-start;
  gap: 13px;
  padding: 18px;
  border: 1px solid #e2e7e4;
  border-radius: 13px;
  background: #ffffff;
}

.kpi-card.attention {
  border-color: #e4e4d8;
  background: linear-gradient(145deg, #ffffff, #fdfcf7);
}

.kpi-icon {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 10px;
}

.kpi-icon .ui-icon {
  width: 17px;
  height: 17px;
}

.kpi-icon.task {
  color: #526d85;
  background: #edf3f8;
}

.kpi-icon.boxes {
  color: #6d5c48;
  background: #f6efe6;
}

.kpi-icon.input {
  color: #906f25;
  background: #fff4d9;
}

.kpi-icon.review {
  color: #725b89;
  background: #f2ecf7;
}

.kpi-icon.release {
  color: #526f9c;
  background: #ebf1fb;
}

.kpi-icon.locked {
  color: #39715a;
  background: #e8f4ed;
}

.kpi-card > div {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.kpi-card span {
  color: #6e7b75;
  font-size: 10px;
}

.kpi-card strong {
  margin: 1px 0;
  color: #263a32;
  font-size: 24px;
  font-weight: 680;
  letter-spacing: -0.035em;
}

.kpi-card small {
  overflow: hidden;
  color: #99a39e;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tower-columns {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(310px, 0.65fr);
  gap: 14px;
}

.tower-panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid #e1e6e3;
  border-radius: 14px;
  background: #ffffff;
}

.section-heading {
  display: flex;
  min-height: 82px;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 16px 19px;
  border-bottom: 1px solid #eef0ef;
}

.section-kicker {
  color: #6e9584;
  font-size: 8px;
  font-weight: 750;
  letter-spacing: 0.14em;
}

.section-heading h3,
.empty-control-tower h3 {
  margin: 5px 0 3px;
  color: #293b34;
  font-size: 14px;
  font-weight: 680;
}

.section-heading p {
  margin: 0;
  color: #8c9691;
  font-size: 9px;
}

.section-count {
  flex: 0 0 auto;
  padding: 5px 9px;
  border-radius: 999px;
  color: #557065;
  background: #f0f5f2;
  font-size: 9px;
  font-weight: 650;
}

.work-queue {
  display: grid;
  padding: 3px 19px;
}

.queue-item {
  display: grid;
  min-height: 82px;
  grid-template-columns: 38px minmax(0, 1fr) 30px;
  gap: 12px;
  align-items: center;
  border-bottom: 1px solid #f0f2f1;
}

.queue-item:last-child {
  border-bottom: 0;
}

.queue-type {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 10px;
  font-size: 9px;
  font-weight: 720;
}

.queue-type.input {
  color: #8a6920;
  background: #fff3d5;
}

.queue-type.review {
  color: #6e5783;
  background: #f1eaf6;
}

.queue-type.release {
  color: #506d9a;
  background: #ebf1fb;
}

.queue-type.failed {
  color: #9a4f4f;
  background: #fbeded;
}

.queue-type.replan {
  color: #965443;
  background: #fbefea;
}

.queue-type.start {
  color: #517064;
  background: #edf4f0;
}

.queue-copy {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.queue-copy > div {
  display: flex;
  align-items: center;
  gap: 8px;
}

.queue-copy > div strong {
  color: #32433c;
  font-size: 10px;
}

.queue-copy > div span {
  color: #9ba39f;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 8px;
}

.queue-copy h4 {
  margin: 0;
  overflow: hidden;
  color: #58665f;
  font-size: 10px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-copy p {
  margin: 0;
  overflow: hidden;
  color: #939d98;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-link {
  display: grid;
  width: 29px;
  height: 29px;
  place-items: center;
  border: 1px solid #e1e7e4;
  border-radius: 8px;
  color: #6b7e75;
  background: #ffffff;
}

.row-link:hover {
  border-color: #bfcfc7;
  color: #356a57;
  background: #f7faf8;
}

.row-link .ui-icon {
  width: 13px;
  height: 13px;
}

.panel-footer-link {
  display: flex;
  min-height: 42px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border-top: 1px solid #eef0ef;
  color: #466e5e;
  background: #fbfcfb;
  font-size: 9px;
  font-weight: 650;
  text-decoration: none;
}

.panel-footer-link .ui-icon {
  width: 12px;
}

.version-list {
  display: grid;
  padding: 3px 18px;
}

.version-item {
  display: grid;
  gap: 10px;
  padding: 15px 0;
  border-bottom: 1px solid #f0f2f1;
}

.version-item:last-child {
  border-bottom: 0;
}

.version-title {
  display: grid;
  grid-template-columns: 35px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
}

.version-title > span {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 9px;
  color: #346852;
  background: #e9f4ee;
  font-size: 10px;
  font-weight: 750;
}

.version-title > div {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.version-title strong {
  overflow: hidden;
  color: #384840;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version-title small {
  color: #9aa39f;
  font-size: 8px;
}

.version-title em {
  padding: 4px 7px;
  border-radius: 999px;
  color: #39715a;
  background: #edf6f1;
  font-size: 8px;
  font-style: normal;
}

.version-item dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin: 0;
}

.version-item dl > div {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.version-item dt {
  color: #a0a8a4;
  font-size: 8px;
}

.version-item dd {
  margin: 0;
  overflow: hidden;
  color: #59675f;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.panel-empty {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 24px;
}

.panel-empty.compact {
  min-height: 250px;
}

.panel-empty > span {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  place-items: center;
  border-radius: 11px;
  color: #618071;
  background: #edf4f0;
}

.panel-empty > span .ui-icon {
  width: 18px;
}

.panel-empty > div {
  display: grid;
  gap: 4px;
}

.panel-empty strong {
  color: #46564f;
  font-size: 11px;
}

.panel-empty p {
  max-width: 300px;
  margin: 0;
  color: #939d98;
  font-size: 9px;
  line-height: 1.6;
}

.table-heading {
  min-height: 76px;
}

.text-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #47705f;
  font-size: 9px;
  font-weight: 650;
  text-decoration: none;
}

.text-link .ui-icon {
  width: 12px;
}

.task-table-wrap {
  overflow-x: auto;
}

.task-table {
  width: 100%;
  min-width: 920px;
  border-collapse: collapse;
}

.task-table th {
  height: 39px;
  padding: 0 14px;
  color: #8b9691;
  background: #fafbfa;
  font-size: 8px;
  font-weight: 650;
  text-align: left;
  white-space: nowrap;
}

.task-table th:first-child,
.task-table td:first-child {
  padding-left: 19px;
}

.task-table th:last-child,
.task-table td:last-child {
  padding-right: 19px;
}

.task-table td {
  height: 76px;
  padding: 10px 14px;
  border-top: 1px solid #f0f2f1;
  color: #4d5b54;
  font-size: 10px;
  vertical-align: middle;
}

.task-table tbody tr:hover {
  background: #fcfdfc;
}

.task-identity,
.scale-cell,
.version-cell,
.situation-cell {
  display: grid;
  gap: 3px;
}

.task-identity strong,
.scale-cell strong,
.version-cell strong {
  color: #34453d;
  font-size: 10px;
  font-weight: 650;
}

.task-identity span {
  color: #939e99;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 8px;
}

.business-status {
  display: inline-flex;
  padding: 4px 7px;
  border: 1px solid #e1e6e3;
  border-radius: 6px;
  color: #66756e;
  background: #f8faf9;
  font-size: 8px;
}

.scale-cell span,
.version-cell span,
.situation-cell small {
  color: #99a29e;
  font-size: 8px;
}

.situation-pill {
  display: inline-flex;
  width: fit-content;
  align-items: center;
  gap: 5px;
  padding: 4px 7px;
  border-radius: 999px;
  font-size: 8px;
  font-weight: 650;
}

.situation-pill i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.situation-pill.warning {
  color: #896a22;
  background: #fff4d9;
}

.situation-pill.review {
  color: #6d5682;
  background: #f1eaf6;
}

.situation-pill.success {
  color: #39715a;
  background: #e9f4ee;
}

.situation-pill.neutral {
  color: #68756f;
  background: #f0f2f1;
}

.situation-pill.danger {
  color: #985050;
  background: #fbeded;
}

.version-cell.locked strong {
  color: #39715a;
}

.version-cell.unknown strong {
  color: #985050;
}

.date-cell {
  color: #77847e !important;
  white-space: nowrap;
}

.empty-control-tower {
  display: flex;
  min-height: 330px;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 36px;
  border: 1px solid #dfe6e2;
  border-radius: 15px;
  background: #ffffff;
}

.empty-illustration {
  display: grid;
  width: 54px;
  height: 54px;
  place-items: center;
  border-radius: 15px;
  color: #4e7665;
  background: #eaf3ee;
}

.empty-illustration .ui-icon {
  width: 25px;
  height: 25px;
}

.empty-control-tower > div {
  max-width: 420px;
}

.empty-control-tower p {
  margin: 0;
  color: #7f8c86;
  font-size: 10px;
  line-height: 1.7;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1180px) {
  .tower-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .tower-actions {
    width: 100%;
  }

  .sync-state {
    margin-right: auto;
    text-align: left;
  }

  .kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 920px) {
  .tower-columns {
    grid-template-columns: 1fr;
  }

  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .tower-header {
    padding: 22px;
  }

  .tower-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .sync-state {
    grid-column: 1 / -1;
    padding: 0 0 12px;
    border-right: 0;
    border-bottom: 1px solid #dfe7e3;
  }

  .kpi-grid {
    grid-template-columns: 1fr;
  }

  .kpi-card {
    min-height: 98px;
  }

  .state-banner,
  .empty-control-tower {
    align-items: flex-start;
    flex-direction: column;
  }

  .queue-item {
    grid-template-columns: 38px minmax(0, 1fr);
  }

  .queue-item .row-link {
    display: none;
  }

  .section-heading {
    align-items: flex-start;
  }
}
</style>
