<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  getShipmentCase,
  type ShipmentCase,
  type ShipmentCaseEvent,
  type ShipmentCaseResourceBinding,
  type ShipmentCaseStatus,
  type ShipmentResourceType,
  type ShipmentTransportScenario,
} from '@/api/platform'
import ShipmentCaseDetailDrawer from '@/components/shipment-case/ShipmentCaseDetailDrawer.vue'
import UiIcon from '@/components/UiIcon.vue'

type StageState = 'done' | 'current' | 'attention' | 'pending' | 'skipped'

interface WorkspaceStage {
  number: string
  title: string
  description: string
  state: StageState
  stateLabel: string
}

interface WorkspaceGuidance {
  tone: 'neutral' | 'attention' | 'success'
  eyebrow: string
  title: string
  description: string
  actions: string[]
}

const route = useRoute()
const detail = ref<ShipmentCase | null>(null)
const loading = ref(false)
const loadError = ref('')
const lastRefreshedAt = ref<Date | null>(null)
const showManageDrawer = ref(false)
let loadRequestSequence = 0

const statusLabels: Record<ShipmentCaseStatus, string> = {
  DRAFT: '草稿',
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
}

const scenarioLabels: Record<ShipmentTransportScenario, string> = {
  UNDECIDED: '运输场景待确认',
  SEA_FCL: '海运整柜',
  SEA_LCL: '海运拼箱',
  AIR: '空运',
  EXPRESS: '国际快递',
  LOAD_PLANNING_ONLY: '仅装载规划',
  BOOKING_ONLY: '仅订舱',
}

const resourceLabels: Record<ShipmentResourceType, string> = {
  LOAD_PLANNING_TASK: '智能装载支线',
  LINK_BOOKING_APPLICATION: 'LINK 订舱支线',
}

const eventLabels: Record<string, string> = {
  CASE_CREATED: '案件已创建',
  RESOURCE_BOUND: '专业资源已关联',
  CASE_STATUS_CHANGED: '案件状态已变更',
}

const newestEvents = computed<ShipmentCaseEvent[]>(() =>
  [...(detail.value?.events ?? [])].sort((left, right) => right.sequence - left.sequence),
)

const loadPlanningBindings = computed(() =>
  bindingsOfType('LOAD_PLANNING_TASK'),
)

const linkBookingBindings = computed(() =>
  bindingsOfType('LINK_BOOKING_APPLICATION'),
)

const completedStageCount = computed(
  () => stages.value.filter((stage) => stage.state === 'done').length,
)

/**
 * 这里只把后端已经记录的案件状态和资源关系整理成可读阶段。
 *
 * 它不是 Shipment Agent 的运行图，也不会把“资源已绑定”推断成“专业任务已完成”。
 * 后续接入总 LangGraph 后，阶段状态应改为读取真实节点和 checkpoint。
 */
const stages = computed<WorkspaceStage[]>(() => {
  const shipmentCase = detail.value
  if (!shipmentCase) return []

  const hasBindings = shipmentCase.resourceBindings.length > 0
  const terminal = ['COMPLETED', 'CANCELLED'].includes(shipmentCase.status)

  return [
    {
      number: '01',
      title: '建立案件',
      description: `案件主数据已登记，当前服务端修订号为 R${shipmentCase.revision}。`,
      state: 'done',
      stateLabel: '已登记',
    },
    {
      number: '02',
      title: '关联专业资源',
      description: hasBindings
        ? `已关联 ${shipmentCase.resourceBindings.length} 项资源；是否齐全仍由业务人员确认。`
        : terminal
          ? '终态前没有登记专业资源，本阶段仅保留为空的历史事实。'
          : '尚未关联装载任务或 LINK 订舱申请。',
      state: hasBindings ? 'done' : terminal ? 'skipped' : 'attention',
      stateLabel: hasBindings ? '已有记录' : terminal ? '未发生' : '待处理',
    },
    {
      number: '03',
      title: '推进专业作业',
      description:
        shipmentCase.status === 'ACTIVE'
          ? '案件处于进行中；请进入专业工作台核验源系统的实时状态。'
          : shipmentCase.status === 'COMPLETED'
            ? '案件已由业务人员标记完成，不代表本页独立验证了各专业结果。'
            : shipmentCase.status === 'CANCELLED'
              ? '案件已取消，不再推进后续专业作业。'
              : '案件仍为草稿，尚未进入人工推进阶段。',
      state:
        shipmentCase.status === 'ACTIVE'
          ? 'current'
          : shipmentCase.status === 'COMPLETED'
            ? 'done'
            : shipmentCase.status === 'CANCELLED'
              ? 'skipped'
              : 'pending',
      stateLabel:
        shipmentCase.status === 'ACTIVE'
          ? '人工推进中'
          : shipmentCase.status === 'COMPLETED'
            ? '人工确认完成'
            : shipmentCase.status === 'CANCELLED'
              ? '已终止'
              : '尚未开始',
    },
    {
      number: '04',
      title: '结案与审计',
      description:
        shipmentCase.status === 'COMPLETED'
          ? '案件已完成，页面保留当前快照和不可变事件时间线。'
          : shipmentCase.status === 'CANCELLED'
            ? '案件已取消，页面保留终止原因和历史审计记录。'
            : '等待业务人员在专业作业结束后确认终态。',
      state: terminal ? 'done' : 'pending',
      stateLabel:
        shipmentCase.status === 'COMPLETED'
          ? '已归档'
          : shipmentCase.status === 'CANCELLED'
            ? '终止归档'
            : '等待结案',
    },
  ]
})

const guidance = computed<WorkspaceGuidance>(() => {
  const shipmentCase = detail.value
  if (!shipmentCase) {
    return {
      tone: 'neutral',
      eyebrow: 'CURRENT GUIDANCE',
      title: '正在读取案件',
      description: '等待权威案件快照返回。',
      actions: [],
    }
  }

  if (shipmentCase.status === 'COMPLETED') {
    return {
      tone: 'success',
      eyebrow: 'CURRENT GUIDANCE',
      title: '案件已完成，当前以审计复核为主',
      description:
        '总 Agent 尚未接入，因此这里不会声称系统自动验收了装载与订舱结果。',
      actions: ['核对专业资源的绑定时快照', '按时间线复核关键人工操作'],
    }
  }

  if (shipmentCase.status === 'CANCELLED') {
    return {
      tone: 'neutral',
      eyebrow: 'CURRENT GUIDANCE',
      title: '案件已取消，不再生成推进建议',
      description: '如需继续同一业务，应由业务人员建立新案件，而不是改写当前终态记录。',
      actions: ['查看取消事件与原因', '保留原案件作为审计依据'],
    }
  }

  if (shipmentCase.transportScenario === 'UNDECIDED') {
    return {
      tone: 'attention',
      eyebrow: 'MANUAL DECISION REQUIRED',
      title: '运输场景仍待确认，当前版本不能直接修改',
      description:
        '当前尚未提供案件场景变更命令，也没有跨域自动追问或回退；页面不会给出一个实际不存在的“更新场景”入口。',
      actions:
        shipmentCase.status === 'DRAFT'
          ? [
              '保持当前案件为草稿，在线下确认海运、空运、快递或单能力场景',
              '场景确认后新建场景正确的案件，并取消当前草稿，避免错误激活',
            ]
          : [
              '暂停继续绑定专业资源，先核对当前案件是否被错误激活',
              '新建场景正确的案件，并在案件操作中取消当前案件以保留审计记录',
            ],
    }
  }

  const missingLoadPlanning =
    shipmentCase.transportScenario === 'LOAD_PLANNING_ONLY' &&
    loadPlanningBindings.value.length === 0
  const missingBooking =
    shipmentCase.transportScenario === 'BOOKING_ONLY' &&
    linkBookingBindings.value.length === 0

  if (missingLoadPlanning || missingBooking) {
    const missingName = missingLoadPlanning ? '装载任务' : 'LINK 订舱申请'
    return {
      tone: 'attention',
      eyebrow: 'RESOURCE MISSING',
      title: `当前单能力场景尚未关联${missingName}`,
      description:
        '这是根据案件场景和绑定记录得出的确定性提醒，不是 Agent 自主规划结果。',
      actions: [`进入对应专业工作台准备${missingName}`, '返回案件操作抽屉验证并关联资源编号'],
    }
  }

  if (shipmentCase.status === 'DRAFT') {
    return {
      tone: 'neutral',
      eyebrow: 'READY FOR HUMAN REVIEW',
      title: '确认案件范围后，可由业务人员激活',
      description:
        '当前版本只提供人工案件编排。激活前请确认运输场景、资源范围和案件说明。',
      actions: ['确认已关联资源是否覆盖本票范围', '在案件操作中填写原因并激活'],
    }
  }

  if (shipmentCase.resourceBindings.length === 0) {
    return {
      tone: 'attention',
      eyebrow: 'RESOURCE VISIBILITY',
      title: '案件已进行中，但尚无专业资源记录',
      description:
        '本页无法据此判断实际业务是否已经开始，请先核对专业系统，再决定是否补充关联。',
      actions: ['核对装载与订舱系统中是否已有任务', '有稳定编号后再绑定，避免创建重复资源'],
    }
  }

  return {
    tone: 'neutral',
    eyebrow: 'HUMAN-ORCHESTRATED CASE',
    title: '进入专业工作台核验实时进度',
    description:
      '本页保存的是绑定时快照，不会把它冒充为源系统实时状态。完成专业作业后再人工结案。',
    actions: ['分别打开装载和订舱支线核验实时结果', '处理完业务异常后，在案件操作中确认结案'],
  }
})

function routeCaseNumber(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '').trim()
  return typeof value === 'string' ? value.trim() : ''
}

async function loadCase(caseNumber = routeCaseNumber(route.params.caseNumber)): Promise<void> {
  const requestSequence = ++loadRequestSequence
  if (!caseNumber) {
    detail.value = null
    loadError.value = '地址中缺少发运案件编号。'
    loading.value = false
    return
  }

  // 同一案件手动刷新失败时保留上一份成功快照，并在页面内提示同步失败；
  // 切换到另一个案件编号时则先清空，避免把旧案件误显示在新 URL 下。
  if (detail.value?.caseNumber !== caseNumber) {
    detail.value = null
  }

  loading.value = true
  loadError.value = ''
  try {
    const response = await getShipmentCase(caseNumber)
    if (
      requestSequence !== loadRequestSequence ||
      routeCaseNumber(route.params.caseNumber) !== caseNumber
    ) {
      return
    }
    detail.value = response
    lastRefreshedAt.value = new Date()
  } catch (error) {
    if (requestSequence !== loadRequestSequence) return
    loadError.value = error instanceof Error ? error.message : '读取发运案件详情失败'
  } finally {
    if (requestSequence === loadRequestSequence) {
      loading.value = false
    }
  }
}

function bindingsOfType(resourceType: ShipmentResourceType): ShipmentCaseResourceBinding[] {
  return (
    detail.value?.resourceBindings.filter(
      (binding) => binding.resourceType === resourceType,
    ) ?? []
  )
}

function onCaseUpdated(changed: ShipmentCase): void {
  // 抽屉写命令已经返回更新后的权威快照；终止更早发出的详情请求，避免旧响应覆盖新 revision。
  loadRequestSequence += 1
  loading.value = false
  detail.value = changed
  lastRefreshedAt.value = new Date()
}

function resourceWorkspacePath(
  resourceType: ShipmentResourceType,
  resourceNumber?: string,
): string {
  if (resourceType === 'LOAD_PLANNING_TASK') {
    return resourceNumber
      ? `/supply-chain/load-planning/tasks?taskNumber=${encodeURIComponent(resourceNumber)}&view=planning`
      : '/supply-chain/load-planning/tasks'
  }
  return resourceNumber
    ? `/link-booking/applications?applicationNumber=${encodeURIComponent(resourceNumber)}&view=detail`
    : '/link-booking/applications'
}

function resourceScopeLabel(resourceType: ShipmentResourceType): string {
  const scenario = detail.value?.transportScenario
  if (!scenario) return '按业务需要'
  if (scenario === 'LOAD_PLANNING_ONLY') {
    return resourceType === 'LOAD_PLANNING_TASK' ? '当前案件范围' : '当前场景非必需'
  }
  if (scenario === 'BOOKING_ONLY') {
    return resourceType === 'LINK_BOOKING_APPLICATION' ? '当前案件范围' : '当前场景非必需'
  }
  if (scenario === 'SEA_FCL') return '可并行推进'
  return '按实际业务需要'
}

function verificationSourceLabel(value: string): string {
  const labels: Record<string, string> = {
    LOCAL_LOAD_PLANNING_DATABASE: 'Agent Runtime 本地数据库',
    LINK_BOOKING_HTTP: 'LINK 只读接口',
  }
  return labels[value] ?? value
}

function eventTitle(event: ShipmentCaseEvent): string {
  return eventLabels[event.eventType] ?? event.eventType
}

function eventStatusTransition(event: ShipmentCaseEvent): string {
  if (!event.currentStatus) return ''
  const current = statusLabels[event.currentStatus]
  if (!event.previousStatus) return current
  return `${statusLabels[event.previousStatus]} → ${current}`
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
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

watch(
  () => route.params.caseNumber,
  (caseNumber) => {
    void loadCase(routeCaseNumber(caseNumber))
  },
  { immediate: true },
)
</script>

<template>
  <section class="case-workspace">
    <nav class="workspace-breadcrumb" aria-label="面包屑">
      <RouterLink to="/tradeops/shipment-cases">
        <UiIcon name="arrow" />
        返回案件中心
      </RouterLink>
      <span>/</span>
      <strong>{{ detail?.caseNumber || routeCaseNumber(route.params.caseNumber) }}</strong>
    </nav>

    <div v-if="loading && !detail" class="workspace-state-card">
      <span class="workspace-loader"></span>
      <div>
        <strong>正在建立案件工作台</strong>
        <p>正在通过 Java BFF 读取 Agent Runtime 中的权威案件快照。</p>
      </div>
    </div>

    <div v-else-if="loadError && !detail" class="workspace-state-card error" role="alert">
      <span>!</span>
      <div>
        <strong>案件工作台加载失败</strong>
        <p>{{ loadError }}</p>
        <div class="workspace-state-actions">
          <RouterLink to="/tradeops/shipment-cases">返回案件中心</RouterLink>
          <button type="button" @click="loadCase()">重新加载</button>
        </div>
      </div>
    </div>

    <template v-else-if="detail">
      <header class="case-hero">
        <div class="case-hero-main">
          <div class="case-kicker-row">
            <span class="case-kicker">SHIPMENT CASE WORKSPACE</span>
            <span class="case-mode-badge">
              <i></i>
              人工案件编排
            </span>
          </div>
          <div class="case-title-row">
            <div>
              <code>{{ detail.caseNumber }}</code>
              <h2>{{ detail.caseName }}</h2>
              <p>{{ detail.description || '暂无业务说明；可在后续案件版本中补充业务背景。' }}</p>
            </div>
            <span class="case-status" :data-status="detail.status">
              <i></i>
              {{ statusLabels[detail.status] }}
            </span>
          </div>
        </div>

        <div class="case-hero-actions">
          <div>
            <span>权威快照同步</span>
            <strong>{{ formatRefreshTime(lastRefreshedAt) }}</strong>
          </div>
          <button type="button" :disabled="loading" @click="loadCase()">
            <UiIcon name="refresh" />
            {{ loading ? '同步中' : '刷新' }}
          </button>
          <button class="primary" type="button" @click="showManageDrawer = true">
            <UiIcon name="layers" />
            案件操作
          </button>
        </div>
      </header>

      <div v-if="loadError" class="inline-load-error" role="alert">
        <strong>刷新失败：</strong>{{ loadError }}
        <button type="button" @click="loadCase()">重试</button>
      </div>

      <section class="agent-boundary-banner">
        <span class="agent-boundary-icon"><UiIcon name="shield" /></span>
        <div>
          <span>ORCHESTRATION MODE</span>
          <strong>当前编排模式：人工确认</strong>
          <p>
            当前阶段由案件状态、资源绑定和审计事件组成，建议为确定性规则提示；
            跨域自动规划、工具调用与异常回退尚未启用。
          </p>
        </div>
        <span class="agent-boundary-tag">关键步骤人工复核</span>
      </section>

      <section class="case-fact-grid" aria-label="案件摘要">
        <article>
          <span>运输场景</span>
          <strong>{{ scenarioLabels[detail.transportScenario] }}</strong>
          <small>{{ detail.transportScenario }}</small>
        </article>
        <article>
          <span>负责组织</span>
          <strong>{{ detail.responsibleOrganizationCode }}</strong>
          <small>真实组织权限尚待 RBAC 接入</small>
        </article>
        <article>
          <span>案件版本</span>
          <strong>R{{ detail.revision }}</strong>
          <small>写命令使用乐观锁校验</small>
        </article>
        <article>
          <span>专业资源</span>
          <strong>{{ detail.resourceBindings.length }}</strong>
          <small>
            {{ loadPlanningBindings.length }} 装载 ·
            {{ linkBookingBindings.length }} 订舱
          </small>
        </article>
        <article>
          <span>审计事件</span>
          <strong>{{ detail.events.length }}</strong>
          <small>只追加，不覆盖历史事实</small>
        </article>
      </section>

      <div class="workspace-grid">
        <section class="workspace-panel stage-panel">
          <div class="panel-heading">
            <div>
              <span>CASE STAGES</span>
              <h3>协同阶段</h3>
              <p>根据案件状态和绑定记录推导，不代表 Agent 节点执行进度。</p>
            </div>
            <div class="stage-count">
              <strong>{{ completedStageCount }}</strong>
              <span>/ {{ stages.length }} 已形成记录</span>
            </div>
          </div>

          <ol class="stage-track">
            <li
              v-for="stage in stages"
              :key="stage.number"
              :data-state="stage.state"
            >
              <div class="stage-marker">
                <UiIcon v-if="stage.state === 'done'" name="check" />
                <span v-else>{{ stage.number }}</span>
              </div>
              <div class="stage-content">
                <div>
                  <strong>{{ stage.title }}</strong>
                  <span>{{ stage.stateLabel }}</span>
                </div>
                <p>{{ stage.description }}</p>
              </div>
            </li>
          </ol>
        </section>

        <aside class="workspace-panel guidance-panel" :data-tone="guidance.tone">
          <div class="guidance-heading">
            <span class="guidance-icon">
              <UiIcon :name="guidance.tone === 'success' ? 'check' : 'alert'" />
            </span>
            <div>
              <span>{{ guidance.eyebrow }}</span>
              <h3>当前建议与阻塞</h3>
            </div>
          </div>
          <strong class="guidance-title">{{ guidance.title }}</strong>
          <p class="guidance-description">{{ guidance.description }}</p>
          <ol v-if="guidance.actions.length" class="guidance-actions">
            <li v-for="(action, index) in guidance.actions" :key="action">
              <span>{{ index + 1 }}</span>
              {{ action }}
            </li>
          </ol>
          <button class="guidance-button" type="button" @click="showManageDrawer = true">
            打开案件操作
            <UiIcon name="arrow" />
          </button>
        </aside>
      </div>

      <section class="workspace-panel resource-panel">
        <div class="panel-heading">
          <div>
            <span>PROFESSIONAL WORKSTREAMS</span>
            <h3>专业资源支线</h3>
            <p>案件保存稳定编号与绑定时快照；实时业务状态仍以各专业工作台为准。</p>
          </div>
          <span class="snapshot-legend">
            <i></i>
            绑定时快照，不是实时状态
          </span>
        </div>

        <div class="workstream-grid">
          <article class="workstream-card">
            <div class="workstream-card-heading">
              <span class="workstream-icon load"><UiIcon name="box" /></span>
              <div>
                <span>LOAD PLANNING</span>
                <h4>{{ resourceLabels.LOAD_PLANNING_TASK }}</h4>
              </div>
              <span class="scope-badge">{{ resourceScopeLabel('LOAD_PLANNING_TASK') }}</span>
            </div>

            <div v-if="loadPlanningBindings.length" class="binding-list">
              <div v-for="binding in loadPlanningBindings" :key="binding.id" class="binding-item">
                <div class="binding-identity">
                  <span>任务编号</span>
                  <strong>{{ binding.resourceNumber }}</strong>
                </div>
                <dl>
                  <div>
                    <dt>绑定时状态快照</dt>
                    <dd>{{ binding.resourceStatusAtVerification }}</dd>
                  </div>
                  <div>
                    <dt>验证来源</dt>
                    <dd>{{ verificationSourceLabel(binding.verificationSource) }}</dd>
                  </div>
                  <div>
                    <dt>验证时间</dt>
                    <dd>{{ formatDateTime(binding.verifiedAt) }}</dd>
                  </div>
                </dl>
                <RouterLink
                  class="binding-direct-link"
                  :to="resourceWorkspacePath(binding.resourceType, binding.resourceNumber)"
                >
                  打开此任务
                  <UiIcon name="arrow" />
                </RouterLink>
              </div>
            </div>
            <div v-else class="binding-empty">
              <UiIcon name="layers" />
              <div>
                <strong>尚未关联装载任务</strong>
                <p>这只表示案件台账没有绑定记录，不代表源系统一定没有任务。</p>
              </div>
            </div>

            <RouterLink
              class="workstream-link"
              :to="resourceWorkspacePath('LOAD_PLANNING_TASK')"
            >
              打开智能装载工作台
              <UiIcon name="arrow" />
            </RouterLink>
          </article>

          <article class="workstream-card">
            <div class="workstream-card-heading">
              <span class="workstream-icon booking"><UiIcon name="database" /></span>
              <div>
                <span>LINK BOOKING</span>
                <h4>{{ resourceLabels.LINK_BOOKING_APPLICATION }}</h4>
              </div>
              <span class="scope-badge">
                {{ resourceScopeLabel('LINK_BOOKING_APPLICATION') }}
              </span>
            </div>

            <div v-if="linkBookingBindings.length" class="binding-list">
              <div v-for="binding in linkBookingBindings" :key="binding.id" class="binding-item">
                <div class="binding-identity">
                  <span>申请编号</span>
                  <strong>{{ binding.resourceNumber }}</strong>
                </div>
                <dl>
                  <div>
                    <dt>绑定时状态快照</dt>
                    <dd>{{ binding.resourceStatusAtVerification }}</dd>
                  </div>
                  <div>
                    <dt>验证来源</dt>
                    <dd>{{ verificationSourceLabel(binding.verificationSource) }}</dd>
                  </div>
                  <div>
                    <dt>验证时间</dt>
                    <dd>{{ formatDateTime(binding.verifiedAt) }}</dd>
                  </div>
                </dl>
                <RouterLink
                  class="binding-direct-link"
                  :to="resourceWorkspacePath(binding.resourceType, binding.resourceNumber)"
                >
                  打开此申请
                  <UiIcon name="arrow" />
                </RouterLink>
              </div>
            </div>
            <div v-else class="binding-empty">
              <UiIcon name="database" />
              <div>
                <strong>尚未关联 LINK 订舱申请</strong>
                <p>请先核对专业系统，确认已有稳定申请编号后再建立关联。</p>
              </div>
            </div>

            <RouterLink
              class="workstream-link"
              :to="resourceWorkspacePath('LINK_BOOKING_APPLICATION')"
            >
              打开 LINK 订舱工作台
              <UiIcon name="arrow" />
            </RouterLink>
          </article>
        </div>
      </section>

      <section class="workspace-panel timeline-panel">
        <div class="panel-heading">
          <div>
            <span>AUDIT TRAIL</span>
            <h3>案件事件时间线</h3>
            <p>每条事件都是服务端已经持久化的历史事实，按最新事件优先显示。</p>
          </div>
          <div class="timeline-range">
            <span>创建 {{ formatDateTime(detail.createdAt) }}</span>
            <strong>更新 {{ formatDateTime(detail.updatedAt) }}</strong>
          </div>
        </div>

        <ol v-if="newestEvents.length" class="event-timeline">
          <li v-for="event in newestEvents" :key="event.id">
            <div class="event-sequence">{{ String(event.sequence).padStart(2, '0') }}</div>
            <div class="event-card">
              <div class="event-card-heading">
                <div>
                  <span>{{ event.eventType }}</span>
                  <strong>{{ eventTitle(event) }}</strong>
                </div>
                <time>{{ formatDateTime(event.occurredAt) }}</time>
              </div>
              <p>{{ event.detail }}</p>
              <div class="event-tags">
                <span v-if="eventStatusTransition(event)" class="event-transition">
                  {{ eventStatusTransition(event) }}
                </span>
                <span v-if="event.resourceType">
                  {{ resourceLabels[event.resourceType] }} · {{ event.resourceNumber }}
                </span>
              </div>
              <div class="event-meta">
                <span>
                  {{ event.actorDisplayName }}
                  <small>{{ event.actorSource }}</small>
                </span>
                <code>{{ event.requestId }}</code>
              </div>
            </div>
          </li>
        </ol>
        <div v-else class="timeline-empty">
          <UiIcon name="clock" />
          <p>当前快照没有可展示的审计事件。</p>
        </div>
      </section>

      <ShipmentCaseDetailDrawer
        v-model="showManageDrawer"
        :case-number="detail.caseNumber"
        @updated="onCaseUpdated"
      />
    </template>
  </section>
</template>

<style scoped>
.case-workspace {
  display: grid;
  gap: 18px;
  color: #17211e;
}

.workspace-breadcrumb {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #89928f;
  font-size: 11px;
}

.workspace-breadcrumb a {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #51605b;
  font-weight: 650;
  text-decoration: none;
}

.workspace-breadcrumb a:hover {
  color: #245f4e;
}

.workspace-breadcrumb a :deep(.ui-icon) {
  width: 14px;
  transform: rotate(180deg);
}

.workspace-breadcrumb strong {
  overflow: hidden;
  max-width: 340px;
  color: #4f5a56;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-state-card {
  display: flex;
  min-height: 260px;
  align-items: center;
  justify-content: center;
  gap: 18px;
  padding: 32px;
  border: 1px solid #e1e7e4;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 14px 42px rgba(27, 52, 44, 0.06);
}

.workspace-state-card > span:not(.workspace-loader) {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 14px;
  color: #9a4545;
  background: #faeeee;
  font-size: 18px;
  font-weight: 750;
}

.workspace-state-card strong,
.workspace-state-card p {
  display: block;
  margin: 0;
}

.workspace-state-card strong {
  font-size: 16px;
}

.workspace-state-card p {
  margin-top: 7px;
  color: #74807c;
  font-size: 12px;
  line-height: 1.7;
}

.workspace-loader {
  width: 28px;
  height: 28px;
  border: 3px solid #dce7e2;
  border-top-color: #356a5a;
  border-radius: 50%;
  animation: workspace-spin 0.8s linear infinite;
}

.workspace-state-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

.workspace-state-actions a,
.workspace-state-actions button {
  min-height: 34px;
  padding: 0 13px;
  border: 1px solid #d8e0dd;
  border-radius: 9px;
  color: #42514c;
  background: #fff;
  cursor: pointer;
  font-size: 11px;
  font-weight: 650;
  line-height: 32px;
  text-decoration: none;
}

.case-hero {
  position: relative;
  display: flex;
  min-height: 205px;
  align-items: stretch;
  justify-content: space-between;
  gap: 24px;
  overflow: hidden;
  padding: 28px 30px;
  border: 1px solid #203a33;
  border-radius: 20px;
  color: #f5faf8;
  background:
    radial-gradient(circle at 82% 12%, rgba(117, 211, 175, 0.17), transparent 30%),
    linear-gradient(135deg, #15231f 0%, #1e3931 64%, #21473b 100%);
  box-shadow: 0 18px 52px rgba(21, 46, 38, 0.16);
}

.case-hero::before,
.case-hero::after {
  position: absolute;
  border: 1px solid rgba(190, 224, 209, 0.1);
  border-radius: 50%;
  content: "";
}

.case-hero::before {
  top: -95px;
  right: 110px;
  width: 280px;
  height: 280px;
}

.case-hero::after {
  right: 26px;
  bottom: -130px;
  width: 240px;
  height: 240px;
}

.case-hero-main,
.case-hero-actions {
  position: relative;
  z-index: 1;
}

.case-hero-main {
  flex: 1;
  min-width: 0;
}

.case-kicker-row,
.case-title-row,
.case-hero-actions {
  display: flex;
  align-items: center;
}

.case-kicker-row {
  gap: 12px;
}

.case-kicker {
  color: #8fb8aa;
  font-size: 9px;
  font-weight: 750;
  letter-spacing: 0.16em;
}

.case-mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 1px solid rgba(184, 218, 204, 0.2);
  border-radius: 999px;
  color: #d6e9e1;
  background: rgba(255, 255, 255, 0.06);
  font-size: 9px;
  font-weight: 650;
}

.case-mode-badge i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f0bd6e;
  box-shadow: 0 0 0 3px rgba(240, 189, 110, 0.12);
}

.case-title-row {
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-top: 24px;
}

.case-title-row code {
  color: #9fc4b6;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  letter-spacing: 0.05em;
}

.case-title-row h2 {
  margin: 8px 0 0;
  color: #fff;
  font-size: clamp(24px, 3vw, 34px);
  font-weight: 660;
  letter-spacing: -0.035em;
}

.case-title-row p {
  max-width: 660px;
  margin: 12px 0 0;
  color: #b6c8c1;
  font-size: 12px;
  line-height: 1.75;
}

.case-status {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  margin-top: 4px;
  padding: 7px 11px;
  border: 1px solid rgba(209, 231, 222, 0.18);
  border-radius: 999px;
  color: #dcebe5;
  background: rgba(255, 255, 255, 0.07);
  font-size: 10px;
  font-weight: 700;
}

.case-status i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #91b5a7;
}

.case-status[data-status="ACTIVE"] i {
  background: #65d0a4;
  box-shadow: 0 0 0 4px rgba(101, 208, 164, 0.12);
}

.case-status[data-status="COMPLETED"] i {
  background: #72aaf2;
}

.case-status[data-status="CANCELLED"] i {
  background: #e78585;
}

.case-status[data-status="DRAFT"] i {
  background: #f0bd6e;
}

.case-hero-actions {
  width: 160px;
  flex: 0 0 auto;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-end;
  gap: 8px;
}

.case-hero-actions > div {
  margin-bottom: auto;
  text-align: right;
}

.case-hero-actions > div span,
.case-hero-actions > div strong {
  display: block;
}

.case-hero-actions > div span {
  color: #89a69b;
  font-size: 9px;
}

.case-hero-actions > div strong {
  margin-top: 5px;
  color: #d6e5df;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
}

.case-hero-actions button {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid rgba(207, 229, 220, 0.2);
  border-radius: 10px;
  color: #dceae5;
  background: rgba(255, 255, 255, 0.06);
  cursor: pointer;
  font-size: 10px;
  font-weight: 650;
}

.case-hero-actions button:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.11);
}

.case-hero-actions button.primary {
  border-color: #68a890;
  color: #14211d;
  background: #b8ddcf;
}

.case-hero-actions button.primary:hover {
  background: #cae9de;
}

.case-hero-actions button:disabled {
  cursor: wait;
  opacity: 0.65;
}

.inline-load-error {
  padding: 11px 14px;
  border: 1px solid #efd2d2;
  border-radius: 11px;
  color: #884444;
  background: #fff5f5;
  font-size: 11px;
}

.inline-load-error button {
  margin-left: 8px;
  padding: 0;
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font-weight: 700;
  text-decoration: underline;
}

.agent-boundary-banner {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 15px;
  padding: 15px 18px;
  border: 1px solid #dce7e2;
  border-radius: 14px;
  background:
    linear-gradient(90deg, rgba(229, 242, 236, 0.68), rgba(255, 255, 255, 0.92));
}

.agent-boundary-icon {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #316353;
  background: #dceee7;
}

.agent-boundary-banner > div > span,
.panel-heading > div > span,
.guidance-heading > div > span,
.workstream-card-heading > div > span,
.event-card-heading > div > span {
  display: block;
  color: #8a9692;
  font-size: 8px;
  font-weight: 750;
  letter-spacing: 0.14em;
}

.agent-boundary-banner strong,
.agent-boundary-banner p {
  display: block;
  margin: 0;
}

.agent-boundary-banner strong {
  margin-top: 4px;
  font-size: 12px;
}

.agent-boundary-banner p {
  margin-top: 4px;
  color: #65736e;
  font-size: 10px;
  line-height: 1.55;
}

.agent-boundary-tag {
  padding: 6px 9px;
  border: 1px solid #d7e2dd;
  border-radius: 999px;
  color: #65736e;
  background: rgba(255, 255, 255, 0.76);
  font-size: 9px;
  font-weight: 650;
  white-space: nowrap;
}

.case-fact-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid #e1e7e4;
  border-radius: 15px;
  background: #fff;
  box-shadow: 0 10px 30px rgba(27, 52, 44, 0.04);
}

.case-fact-grid article {
  min-width: 0;
  padding: 17px 18px;
  border-right: 1px solid #edf0ef;
}

.case-fact-grid article:last-child {
  border-right: 0;
}

.case-fact-grid span,
.case-fact-grid strong,
.case-fact-grid small {
  display: block;
}

.case-fact-grid span {
  color: #8a9491;
  font-size: 9px;
}

.case-fact-grid strong {
  overflow: hidden;
  margin-top: 7px;
  color: #24312d;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.case-fact-grid small {
  overflow: hidden;
  margin-top: 5px;
  color: #9aa39f;
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.75fr) minmax(285px, 0.9fr);
  gap: 18px;
}

.workspace-panel {
  min-width: 0;
  border: 1px solid #e1e7e4;
  border-radius: 17px;
  background: #fff;
  box-shadow: 0 12px 36px rgba(27, 52, 44, 0.045);
}

.stage-panel,
.guidance-panel,
.resource-panel,
.timeline-panel {
  padding: 22px;
}

.panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.panel-heading h3,
.guidance-heading h3 {
  margin: 5px 0 0;
  color: #23302c;
  font-size: 16px;
  font-weight: 680;
  letter-spacing: -0.02em;
}

.panel-heading p {
  margin: 7px 0 0;
  color: #7d8884;
  font-size: 10px;
  line-height: 1.55;
}

.stage-count {
  flex: 0 0 auto;
  text-align: right;
}

.stage-count strong,
.stage-count span {
  display: block;
}

.stage-count strong {
  color: #2f6453;
  font-size: 25px;
  font-weight: 650;
  line-height: 1;
}

.stage-count span {
  margin-top: 5px;
  color: #9aa29f;
  font-size: 8px;
}

.stage-track {
  display: grid;
  gap: 0;
  margin: 24px 0 0;
  padding: 0;
  list-style: none;
}

.stage-track li {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 13px;
  padding-bottom: 19px;
}

.stage-track li:last-child {
  padding-bottom: 0;
}

.stage-track li:not(:last-child)::before {
  position: absolute;
  top: 32px;
  bottom: 0;
  left: 16px;
  width: 1px;
  background: #e1e8e5;
  content: "";
}

.stage-track li[data-state="done"]:not(:last-child)::before {
  background: #8bbba9;
}

.stage-marker {
  position: relative;
  z-index: 1;
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid #dce3e0;
  border-radius: 11px;
  color: #8b9692;
  background: #f7f9f8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9px;
  font-weight: 700;
}

.stage-marker :deep(.ui-icon) {
  width: 15px;
}

.stage-track li[data-state="done"] .stage-marker {
  border-color: #c7e0d6;
  color: #2e6855;
  background: #e5f2ed;
}

.stage-track li[data-state="current"] .stage-marker {
  border-color: #7daf9c;
  color: #fff;
  background: #3f7965;
  box-shadow: 0 0 0 4px #e7f1ed;
}

.stage-track li[data-state="attention"] .stage-marker {
  border-color: #efd5aa;
  color: #95601b;
  background: #fff6e8;
}

.stage-track li[data-state="skipped"] .stage-marker {
  color: #a5aba9;
  background: #f3f4f4;
}

.stage-content {
  min-width: 0;
  padding-top: 1px;
}

.stage-content > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.stage-content strong {
  color: #34413d;
  font-size: 11px;
}

.stage-content span {
  padding: 4px 7px;
  border-radius: 999px;
  color: #77817e;
  background: #f2f4f3;
  font-size: 8px;
  font-weight: 650;
  white-space: nowrap;
}

.stage-track li[data-state="current"] .stage-content span {
  color: #2f6855;
  background: #e5f2ed;
}

.stage-track li[data-state="attention"] .stage-content span {
  color: #916121;
  background: #fff4e2;
}

.stage-content p {
  margin: 6px 0 0;
  color: #7d8783;
  font-size: 9px;
  line-height: 1.55;
}

.guidance-panel {
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(155deg, #f8fbfa 0%, #fff 52%),
    #fff;
}

.guidance-panel::after {
  position: absolute;
  top: -65px;
  right: -65px;
  width: 145px;
  height: 145px;
  border-radius: 50%;
  background: rgba(88, 143, 122, 0.07);
  content: "";
}

.guidance-panel[data-tone="attention"] {
  border-color: #ead9bd;
  background: linear-gradient(155deg, #fffaf1 0%, #fff 56%);
}

.guidance-panel[data-tone="success"] {
  border-color: #cfe2da;
}

.guidance-heading {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 11px;
}

.guidance-icon {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 12px;
  color: #356b58;
  background: #e2f0ea;
}

.guidance-panel[data-tone="attention"] .guidance-icon {
  color: #9b661e;
  background: #fff0d7;
}

.guidance-title {
  position: relative;
  z-index: 1;
  display: block;
  margin-top: 22px;
  color: #283530;
  font-size: 14px;
  line-height: 1.45;
}

.guidance-description {
  position: relative;
  z-index: 1;
  margin: 9px 0 0;
  color: #717d78;
  font-size: 10px;
  line-height: 1.7;
}

.guidance-actions {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 9px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
}

.guidance-actions li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: #4e5c57;
  font-size: 9px;
  line-height: 1.5;
}

.guidance-actions li span {
  display: grid;
  width: 19px;
  height: 19px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 6px;
  color: #376a58;
  background: #e6f1ed;
  font-size: 8px;
  font-weight: 750;
}

.guidance-button {
  position: relative;
  z-index: 1;
  display: flex;
  width: 100%;
  min-height: 38px;
  align-items: center;
  justify-content: space-between;
  margin-top: 20px;
  padding: 0 12px;
  border: 1px solid #d7e2de;
  border-radius: 10px;
  color: #315f50;
  background: #fff;
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
}

.guidance-button:hover {
  border-color: #aec9bf;
  background: #f8fbfa;
}

.snapshot-legend {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border: 1px solid #e1e6e4;
  border-radius: 999px;
  color: #7a8480;
  background: #fafbfb;
  font-size: 8px;
  font-weight: 650;
}

.snapshot-legend i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e0a94f;
}

.workstream-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 20px;
}

.workstream-card {
  display: flex;
  min-width: 0;
  min-height: 280px;
  flex-direction: column;
  padding: 18px;
  border: 1px solid #e5eae8;
  border-radius: 14px;
  background: #fbfcfc;
}

.workstream-card-heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}

.workstream-icon {
  display: grid;
  width: 39px;
  height: 39px;
  place-items: center;
  border-radius: 12px;
}

.workstream-icon.load {
  color: #466a92;
  background: #e8f0f9;
}

.workstream-icon.booking {
  color: #6a568a;
  background: #f0ebf7;
}

.workstream-card-heading h4 {
  margin: 4px 0 0;
  color: #303d38;
  font-size: 12px;
}

.scope-badge {
  padding: 5px 7px;
  border-radius: 999px;
  color: #6e7975;
  background: #edf1ef;
  font-size: 8px;
  font-weight: 650;
  white-space: nowrap;
}

.binding-list {
  display: grid;
  gap: 9px;
  margin-top: 17px;
}

.binding-item {
  padding: 13px;
  border: 1px solid #e4e9e7;
  border-radius: 11px;
  background: #fff;
}

.binding-identity {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.binding-identity span {
  color: #8c9692;
  font-size: 8px;
}

.binding-identity strong {
  overflow: hidden;
  color: #34433e;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.binding-item dl {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 9px;
  margin: 12px 0 0;
  padding-top: 11px;
  border-top: 1px solid #eef1f0;
}

.binding-item dt,
.binding-item dd {
  overflow: hidden;
  margin: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.binding-item dt {
  color: #a0a8a5;
  font-size: 7px;
}

.binding-item dd {
  margin-top: 4px;
  color: #66716d;
  font-size: 8px;
}

.binding-direct-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-top: 11px;
  color: #356a58;
  font-size: 9px;
  font-weight: 700;
  text-decoration: none;
}

.binding-direct-link:hover {
  color: #1f4f3f;
}

.binding-direct-link :deep(.ui-icon) {
  width: 12px;
}

.binding-empty {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 11px;
  margin-top: 17px;
  padding: 17px;
  border: 1px dashed #d9e0dd;
  border-radius: 11px;
  color: #8d9894;
  background: #fff;
}

.binding-empty > :deep(.ui-icon) {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
  opacity: 0.72;
}

.binding-empty strong,
.binding-empty p {
  display: block;
  margin: 0;
}

.binding-empty strong {
  color: #5e6a66;
  font-size: 10px;
}

.binding-empty p {
  margin-top: 5px;
  font-size: 8px;
  line-height: 1.55;
}

.workstream-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding: 11px 2px 0;
  border-top: 1px solid #e6ebe9;
  color: #376958;
  font-size: 9px;
  font-weight: 700;
  text-decoration: none;
}

.workstream-link:hover {
  color: #214f40;
}

.workstream-link :deep(.ui-icon) {
  width: 14px;
}

.timeline-range {
  flex: 0 0 auto;
  text-align: right;
}

.timeline-range span,
.timeline-range strong {
  display: block;
  color: #969f9c;
  font-size: 8px;
  font-weight: 500;
}

.timeline-range strong {
  margin-top: 5px;
  color: #68736f;
}

.event-timeline {
  display: grid;
  gap: 12px;
  margin: 22px 0 0;
  padding: 0;
  list-style: none;
}

.event-timeline li {
  display: grid;
  grid-template-columns: 33px minmax(0, 1fr);
  gap: 12px;
}

.event-sequence {
  display: grid;
  width: 33px;
  height: 33px;
  place-items: center;
  border: 1px solid #dce4e1;
  border-radius: 10px;
  color: #557066;
  background: #f2f6f4;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 9px;
  font-weight: 750;
}

.event-card {
  min-width: 0;
  padding: 14px 15px;
  border: 1px solid #e6eae9;
  border-radius: 12px;
  background: #fcfdfd;
}

.event-card-heading,
.event-meta {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.event-card-heading strong {
  display: block;
  margin-top: 4px;
  color: #35423e;
  font-size: 11px;
}

.event-card-heading time {
  flex: 0 0 auto;
  color: #909995;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 8px;
}

.event-card > p {
  margin: 10px 0 0;
  color: #68746f;
  font-size: 9px;
  line-height: 1.65;
}

.event-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.event-tags span {
  padding: 4px 7px;
  border-radius: 7px;
  color: #5d6d67;
  background: #edf2f0;
  font-size: 8px;
}

.event-tags span.event-transition {
  color: #376553;
  background: #e5f1ec;
}

.event-meta {
  align-items: center;
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px solid #ecefee;
}

.event-meta > span {
  color: #5d6965;
  font-size: 8px;
}

.event-meta small {
  margin-left: 5px;
  color: #9ba3a0;
  font-size: 7px;
}

.event-meta code {
  overflow: hidden;
  max-width: 55%;
  color: #9aa29f;
  font-size: 7px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.timeline-empty {
  display: flex;
  min-height: 140px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #929c98;
  font-size: 10px;
}

.timeline-empty p {
  margin: 0;
}

@keyframes workspace-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1080px) {
  .case-fact-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .case-fact-grid article:nth-child(3) {
    border-right: 0;
  }

  .case-fact-grid article:nth-child(n + 4) {
    border-top: 1px solid #edf0ef;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 820px) {
  .case-hero {
    flex-direction: column;
  }

  .case-hero-actions {
    width: 100%;
    flex-direction: row;
    flex-wrap: wrap;
  }

  .case-hero-actions > div {
    width: 100%;
    margin-bottom: 4px;
    text-align: left;
  }

  .case-hero-actions button {
    flex: 1;
  }

  .agent-boundary-banner {
    grid-template-columns: auto 1fr;
  }

  .agent-boundary-tag {
    grid-column: 2;
    justify-self: start;
  }

  .workstream-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 600px) {
  .case-hero,
  .stage-panel,
  .guidance-panel,
  .resource-panel,
  .timeline-panel {
    padding: 18px;
  }

  .case-title-row {
    flex-direction: column;
  }

  .case-fact-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .case-fact-grid article,
  .case-fact-grid article:nth-child(3) {
    border-right: 1px solid #edf0ef;
    border-top: 1px solid #edf0ef;
  }

  .case-fact-grid article:nth-child(odd) {
    border-left: 0;
  }

  .case-fact-grid article:nth-child(even) {
    border-right: 0;
  }

  .case-fact-grid article:nth-child(-n + 2) {
    border-top: 0;
  }

  .agent-boundary-banner {
    grid-template-columns: 1fr;
  }

  .agent-boundary-tag {
    grid-column: 1;
  }

  .panel-heading {
    flex-direction: column;
  }

  .stage-count,
  .timeline-range {
    text-align: left;
  }

  .binding-item dl {
    grid-template-columns: 1fr;
  }

  .event-meta {
    align-items: flex-start;
    flex-direction: column;
  }

  .event-meta code {
    max-width: 100%;
  }
}
</style>
