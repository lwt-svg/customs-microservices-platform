<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  getShipmentCases,
  type ShipmentCase,
  type ShipmentCaseStatus,
  type ShipmentResourceType,
  type ShipmentTransportScenario,
} from '@/api/platform'
import ShipmentCaseCreateDrawer from '@/components/shipment-case/ShipmentCaseCreateDrawer.vue'
import UiIcon from '@/components/UiIcon.vue'

const router = useRouter()
const shipmentCases = ref<ShipmentCase[]>([])
const loading = ref(true)
const loadError = ref('')
const lastRefreshedAt = ref<Date | null>(null)
const hasSuccessfulSnapshot = ref(false)

const keyword = ref('')
const statusFilter = ref<ShipmentCaseStatus | ''>('')
const scenarioFilter = ref<ShipmentTransportScenario | ''>('')
const onlyUnbound = ref(false)

const showCreateDrawer = ref(false)

const statusLabels: Record<ShipmentCaseStatus, string> = {
  DRAFT: '草稿',
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

const scenarioLabels: Record<ShipmentTransportScenario, string> = {
  UNDECIDED: '待确认',
  SEA_FCL: '海运整柜',
  SEA_LCL: '海运拼箱',
  AIR: '空运',
  EXPRESS: '国际快递',
  LOAD_PLANNING_ONLY: '仅装载规划',
  BOOKING_ONLY: '仅订舱',
}

const resourceLabels: Record<ShipmentResourceType, string> = {
  LOAD_PLANNING_TASK: '装载',
  LINK_BOOKING_APPLICATION: '订舱',
}

const resourceTypes: ShipmentResourceType[] = [
  'LOAD_PLANNING_TASK',
  'LINK_BOOKING_APPLICATION',
]

const totalCount = computed(() => shipmentCases.value.length)
const draftCount = computed(
  () => shipmentCases.value.filter((shipmentCase) => shipmentCase.status === 'DRAFT').length,
)
const activeCount = computed(
  () => shipmentCases.value.filter((shipmentCase) => shipmentCase.status === 'ACTIVE').length,
)
const unboundCount = computed(
  () =>
    shipmentCases.value.filter((shipmentCase) => shipmentCase.resourceBindings.length === 0)
      .length,
)

const filteredCases = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLocaleLowerCase()

  return shipmentCases.value.filter((shipmentCase) => {
    const matchesKeyword =
      !normalizedKeyword ||
      shipmentCase.caseNumber.toLocaleLowerCase().includes(normalizedKeyword) ||
      shipmentCase.caseName.toLocaleLowerCase().includes(normalizedKeyword) ||
      shipmentCase.responsibleOrganizationCode.toLocaleLowerCase().includes(normalizedKeyword)
    const matchesStatus = !statusFilter.value || shipmentCase.status === statusFilter.value
    const matchesScenario =
      !scenarioFilter.value || shipmentCase.transportScenario === scenarioFilter.value
    const matchesBinding = !onlyUnbound.value || shipmentCase.resourceBindings.length === 0
    return matchesKeyword && matchesStatus && matchesScenario && matchesBinding
  })
})

const hasFilters = computed(
  () =>
    Boolean(keyword.value) ||
    Boolean(statusFilter.value) ||
    Boolean(scenarioFilter.value) ||
    onlyUnbound.value,
)

async function loadCases(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    shipmentCases.value = sortCases(await getShipmentCases())
    lastRefreshedAt.value = new Date()
    hasSuccessfulSnapshot.value = true
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取发运案件失败'
  } finally {
    loading.value = false
  }
}

function sortCases(cases: ShipmentCase[]): ShipmentCase[] {
  return [...cases].sort(
    (left, right) =>
      new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime() ||
      right.caseNumber.localeCompare(left.caseNumber),
  )
}

function clearFilters(): void {
  keyword.value = ''
  statusFilter.value = ''
  scenarioFilter.value = ''
  onlyUnbound.value = false
}

function showAllCases(): void {
  clearFilters()
}

function showStatus(status: ShipmentCaseStatus): void {
  clearFilters()
  statusFilter.value = status
}

function showUnboundCases(): void {
  clearFilters()
  onlyUnbound.value = true
}

function openDetail(caseNumber: string): void {
  void router.push({
    name: 'shipment-case-workspace',
    params: { caseNumber },
  })
}

function replaceCase(changed: ShipmentCase): void {
  const index = shipmentCases.value.findIndex(
    (shipmentCase) => shipmentCase.id === changed.id,
  )
  if (index >= 0) {
    shipmentCases.value.splice(index, 1, changed)
  } else {
    shipmentCases.value.push(changed)
  }
  shipmentCases.value = sortCases(shipmentCases.value)
}

function onCaseCreated(created: ShipmentCase): void {
  replaceCase(created)
  openDetail(created.caseNumber)
}

function countResources(
  shipmentCase: ShipmentCase,
  resourceType: ShipmentResourceType,
): number {
  return shipmentCase.resourceBindings.filter(
    (binding) => binding.resourceType === resourceType,
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
    hour12: false,
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
    hour12: false,
  }).format(value)
}

function metricCount(value: number): number | string {
  return hasSuccessfulSnapshot.value ? value : '—'
}

onMounted(loadCases)
</script>

<template>
  <section class="shipment-cases-page">
    <header class="shipment-page-header">
      <div class="shipment-page-heading">
        <div class="shipment-breadcrumb">
          <span>TradeOps</span>
          <i></i>
          <strong>发运协同</strong>
        </div>
        <h2>发运案件中心</h2>
        <p>以一票案件统一关联装载、订舱与后续作业，集中保留状态、责任和审计记录。</p>
      </div>

      <div class="shipment-page-actions">
        <div class="shipment-sync-state">
          <span>最近同步</span>
          <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
        </div>
        <button class="secondary-button" type="button" :disabled="loading" @click="loadCases">
          <UiIcon name="refresh" />
          {{ loading ? '同步中' : '刷新数据' }}
        </button>
        <button class="primary-button" type="button" @click="showCreateDrawer = true">
          <UiIcon name="plus" />
          新建发运案件
        </button>
      </div>
    </header>

    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}</p>
      <button type="button" @click="loadCases">重新加载</button>
    </div>

    <section class="shipment-kpi-grid" aria-label="发运案件关键指标">
      <button
        class="shipment-kpi-card"
        :class="{ active: !hasFilters }"
        type="button"
        @click="showAllCases"
      >
        <span class="shipment-kpi-icon total"><UiIcon name="truck" /></span>
        <span>
          <small>案件总数</small>
          <strong>{{ metricCount(totalCount) }}</strong>
          <em>全部纳管发运案件</em>
        </span>
      </button>
      <button
        class="shipment-kpi-card"
        :class="{ active: statusFilter === 'DRAFT' }"
        type="button"
        @click="showStatus('DRAFT')"
      >
        <span class="shipment-kpi-icon draft"><UiIcon name="file" /></span>
        <span>
          <small>草稿案件</small>
          <strong>{{ metricCount(draftCount) }}</strong>
          <em>等待确认范围或启动</em>
        </span>
      </button>
      <button
        class="shipment-kpi-card"
        :class="{ active: statusFilter === 'ACTIVE' }"
        type="button"
        @click="showStatus('ACTIVE')"
      >
        <span class="shipment-kpi-icon active"><UiIcon name="layers" /></span>
        <span>
          <small>进行中</small>
          <strong>{{ metricCount(activeCount) }}</strong>
          <em>正在推进专业作业</em>
        </span>
      </button>
      <button
        class="shipment-kpi-card"
        :class="{ active: onlyUnbound }"
        type="button"
        @click="showUnboundCases"
      >
        <span class="shipment-kpi-icon unbound"><UiIcon name="alert" /></span>
        <span>
          <small>未绑定资源</small>
          <strong>{{ metricCount(unboundCount) }}</strong>
          <em>尚无装载或订舱引用</em>
        </span>
      </button>
    </section>

    <section class="panel shipment-list-panel">
      <div class="shipment-list-heading">
        <div>
          <span>发运案件</span>
          <h3>案件队列</h3>
          <p>显示 {{ filteredCases.length }} / {{ shipmentCases.length }} 条案件</p>
        </div>
        <span class="shipment-service-state" :class="{ error: Boolean(loadError) }">
          <i></i>
          {{ loadError ? '数据同步异常' : '案件数据已同步' }}
        </span>
      </div>

      <div class="shipment-filterbar">
        <label class="shipment-search-box">
          <UiIcon name="search" />
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索案件编号、名称或负责组织"
          />
        </label>
        <label class="shipment-select-filter">
          <span>案件状态</span>
          <select v-model="statusFilter">
            <option value="">全部状态</option>
            <option value="DRAFT">草稿</option>
            <option value="ACTIVE">进行中</option>
            <option value="COMPLETED">已完成</option>
            <option value="CANCELLED">已取消</option>
          </select>
        </label>
        <label class="shipment-select-filter">
          <span>运输场景</span>
          <select v-model="scenarioFilter">
            <option value="">全部场景</option>
            <option value="UNDECIDED">待确认</option>
            <option value="SEA_FCL">海运整柜</option>
            <option value="SEA_LCL">海运拼箱</option>
            <option value="AIR">空运</option>
            <option value="EXPRESS">国际快递</option>
            <option value="LOAD_PLANNING_ONLY">仅装载规划</option>
            <option value="BOOKING_ONLY">仅订舱</option>
          </select>
        </label>
        <label class="shipment-unbound-filter">
          <input v-model="onlyUnbound" type="checkbox" />
          仅看未绑定资源
        </label>
        <button
          v-if="hasFilters"
          class="shipment-clear-filter"
          type="button"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </div>

      <div v-if="loading && shipmentCases.length === 0" class="empty-state">
        <span class="loading-ring"></span>
        <strong>正在同步发运案件</strong>
        <p>正在读取案件、专业作业和最新状态，请稍候。</p>
      </div>

      <div
        v-else-if="loadError && shipmentCases.length === 0"
        class="shipment-empty-state"
      >
        <span><UiIcon name="alert" /></span>
        <div>
          <strong>当前无法判断是否存在发运案件</strong>
          <p>案件来源读取失败；这里不会把未知状态显示成“零条案件”。</p>
        </div>
        <button class="secondary-button" type="button" @click="loadCases">重新加载</button>
      </div>

      <div v-else-if="shipmentCases.length === 0" class="shipment-empty-state">
        <span><UiIcon name="truck" /></span>
        <div>
          <strong>当前没有发运案件</strong>
          <p>创建第一票案件后，即可关联装载任务和 LINK 订舱申请。</p>
        </div>
        <button class="primary-button" type="button" @click="showCreateDrawer = true">
          <UiIcon name="plus" />
          新建发运案件
        </button>
      </div>

      <div v-else-if="filteredCases.length === 0" class="shipment-empty-state compact">
        <span><UiIcon name="search" /></span>
        <div>
          <strong>没有符合条件的案件</strong>
          <p>请调整关键词、状态、运输场景或资源筛选条件。</p>
        </div>
        <button class="secondary-button" type="button" @click="clearFilters">清除筛选</button>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table shipment-case-table">
          <thead>
            <tr>
              <th>案件</th>
              <th>运输场景</th>
              <th>负责组织</th>
              <th>专业资源</th>
              <th>状态</th>
              <th>修订</th>
              <th>最近更新</th>
              <th class="table-actions-header">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="shipmentCase in filteredCases" :key="shipmentCase.id">
              <td>
                <button
                  class="shipment-case-identity-cell"
                  type="button"
                  @click="openDetail(shipmentCase.caseNumber)"
                >
                  <strong>{{ shipmentCase.caseName }}</strong>
                  <code>{{ shipmentCase.caseNumber }}</code>
                </button>
              </td>
              <td>
                <div class="shipment-scenario-cell">
                  <strong>{{ scenarioLabels[shipmentCase.transportScenario] }}</strong>
                  <small>{{ shipmentCase.transportScenario }}</small>
                </div>
              </td>
              <td>
                <span class="shipment-organization-code">
                  {{ shipmentCase.responsibleOrganizationCode }}
                </span>
              </td>
              <td>
                <div class="shipment-resource-chips">
                  <span
                    v-for="resourceType in resourceTypes"
                    :key="resourceType"
                    :class="{
                      empty: countResources(shipmentCase, resourceType) === 0,
                    }"
                  >
                    {{ resourceLabels[resourceType] }}
                    <b>{{ countResources(shipmentCase, resourceType) }}</b>
                  </span>
                </div>
              </td>
              <td>
                <span class="shipment-status-pill compact" :data-status="shipmentCase.status">
                  <i></i>
                  {{ statusLabels[shipmentCase.status] }}
                </span>
              </td>
              <td><code class="shipment-revision">R{{ shipmentCase.revision }}</code></td>
              <td>{{ formatDateTime(shipmentCase.updatedAt) }}</td>
              <td>
                <button
                  class="table-action-button"
                  type="button"
                  @click="openDetail(shipmentCase.caseNumber)"
                >
                  查看详情
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <ShipmentCaseCreateDrawer
      v-model="showCreateDrawer"
      @created="onCaseCreated"
    />
  </section>
</template>
