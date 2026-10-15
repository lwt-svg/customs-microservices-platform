<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import {
  changeLinkBookingApplicationStatus,
  createLinkBookingApplication,
  getLinkBookingApplicationDetail,
  getLinkBookingApplications,
  retryLinkCarrierSubmission,
  type LinkBookingApplication,
  type LinkBookingApplicationDetail,
  type LinkBookingTargetStatus,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import PdfImportDrawer from '@/components/link-booking/PdfImportDrawer.vue'
import LinkBookingWizard from '@/components/link-booking/LinkBookingWizard.vue'

// 页面列表状态：只保存 Java BFF 已经返回到浏览器的数据。
const applications = ref<LinkBookingApplication[]>([])
const loading = ref(true)
const loadError = ref('')
const keyword = ref('')
const statusFilter = ref('')

// 创建抽屉拥有独立的提交状态和错误信息，避免一次提交失败影响列表的加载状态。
const showCreateForm = ref(false)
const showPdfImport = ref(false)
const showLinkWizard = ref(false)
const wizardApplication = ref<LinkBookingApplication | null>(null)
const submitting = ref(false)
const formError = ref('')
const successMessage = ref('')
const changingApplicationNumber = ref('')
const transitionError = ref('')
const showDetail = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref<LinkBookingApplicationDetail | null>(null)
const retryingCarrierSubmission = ref(false)
const carrierRetryError = ref('')
let detailPollTimer: ReturnType<typeof setInterval> | null = null
const pendingTransition = ref<{
  application: LinkBookingApplication
  targetStatus: LinkBookingTargetStatus
} | null>(null)

const form = reactive({
  applicationNumber: '',
  originPort: '',
  destinationPort: '',
  transportMode: 'SEA',
  containerType: '40HQ',
  requestedEtd: '',
})

const statusLabels: Record<LinkBookingApplication['status'], string> = {
  DRAFT: '草稿',
  SUBMITTED: '已提交',
  CONFIRMED: '已确认',
  REJECTED: '已拒绝',
  CANCELLED: '已取消',
}

const eventLabels: Record<string, string> = {
  DRAFT_CREATED: '创建草稿',
  SUBMITTED: '提交申请',
  CONFIRMED: '承运人确认',
  REJECTED: '承运人拒绝',
  CANCELLED: '取消申请',
}

/**
 * 页面只展示当前状态可执行的按钮；真正的状态机仍由 Python 复核。
 *
 * 提交后由 Python 创建异步投递任务；最终确认或拒绝来自独立模拟承运人的回调。
 */
const transitionMeta: Record<
  LinkBookingTargetStatus,
  {
    actionLabel: string
    dialogTitle: string
    description: string
    successText: string
    confirmLabel: string
    destructive: boolean
  }
> = {
  SUBMITTED: {
    actionLabel: '提交',
    dialogTitle: '提交订舱申请',
    description: '提交后申请进入“已提交”状态，等待模拟承运人异步受理和回调。',
    successText: '已提交，等待承运人处理。',
    confirmLabel: '确认提交',
    destructive: false,
  },
  CANCELLED: {
    actionLabel: '取消',
    dialogTitle: '取消订舱申请',
    description: '取消后申请将成为终态，当前学习版本不提供恢复操作。',
    successText: '已取消。',
    confirmLabel: '确认取消',
    destructive: true,
  },
}

const pendingTransitionMeta = computed(() =>
  pendingTransition.value ? transitionMeta[pendingTransition.value.targetStatus] : null,
)

/**
 * 当前搜索和状态筛选只处理已经加载到浏览器的列表，不会发送新的后端请求。
 *
 * 数据量增大并进入分页章节后，再把 keyword、status 作为查询参数交给 Java/Python。
 */
const filteredApplications = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLowerCase()

  return applications.value.filter((application) => {
    const matchesKeyword =
      !normalizedKeyword ||
      application.applicationNumber.toLowerCase().includes(normalizedKeyword) ||
      application.originPort.toLowerCase().includes(normalizedKeyword) ||
      application.destinationPort.toLowerCase().includes(normalizedKeyword)
    const matchesStatus = !statusFilter.value || application.status === statusFilter.value
    return matchesKeyword && matchesStatus
  })
})

/** 只访问 Java BFF；Java 再通过 Nacos 找到 Python LINK。 */
async function loadApplications(): Promise<void> {
  loading.value = true
  loadError.value = ''

  try {
    applications.value = await getLinkBookingApplications()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取 LINK 订舱申请失败'
  } finally {
    loading.value = false
  }
}

/** 打开抽屉时清除上一次表单错误，但不清空列表上的成功反馈。 */
function openCreateForm(): void {
  formError.value = ''
  showCreateForm.value = true
}

/**
 * 浏览器端校验用于尽早给用户提示，不能替代 Java 和 Python 的再次校验。
 */
function validateForm(): string {
  const applicationNumber = form.applicationNumber.trim()
  if (!applicationNumber) return '请输入申请编号。'
  if (!/^DEMO-[A-Za-z0-9_-]+$/.test(applicationNumber)) {
    return '学习环境的申请编号必须以 DEMO- 开头，且只能包含字母、数字、下划线和短横线。'
  }
  if (applicationNumber.length > 64) return '申请编号不能超过 64 个字符。'
  if (!form.originPort.trim()) return '请输入起运港。'
  if (form.originPort.trim().length > 64) return '起运港不能超过 64 个字符。'
  if (!form.destinationPort.trim()) return '请输入目的港。'
  if (form.destinationPort.trim().length > 64) return '目的港不能超过 64 个字符。'
  if (!form.transportMode.trim()) return '请选择运输方式。'
  if (!form.containerType.trim()) return '请选择箱型。'
  if (!form.requestedEtd) return '请选择计划离港日期，模拟 LINK 自动填单需要该日期。'
  return ''
}

function resetForm(): void {
  form.applicationNumber = ''
  form.originPort = ''
  form.destinationPort = ''
  form.transportMode = 'SEA'
  form.containerType = '40HQ'
  form.requestedEtd = ''
  formError.value = ''
}

/**
 * 提交链路：
 * Vue camelCase → Java BFF → Python snake_case → LINK MySQL。
 *
 * Python 保存成功后会返回数据库生成的 id、DRAFT 状态和时间。页面直接把这个权威响应
 * 放到列表开头，不自己伪造 id 或状态。
 */
async function submitApplication(): Promise<void> {
  formError.value = validateForm()
  successMessage.value = ''
  if (formError.value) return

  submitting.value = true
  try {
    const created = await createLinkBookingApplication({
      applicationNumber: form.applicationNumber.trim(),
      originPort: form.originPort.trim(),
      destinationPort: form.destinationPort.trim(),
      transportMode: form.transportMode,
      containerType: form.containerType,
      requestedEtd: form.requestedEtd || null,
    })
    applications.value = [created, ...applications.value]
    successMessage.value = `订舱草稿“${created.applicationNumber}”创建成功。`
    showCreateForm.value = false
    resetForm()
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建 LINK 订舱草稿失败'
  } finally {
    submitting.value = false
  }
}

/** PDF 导入成功后直接使用 Python 返回的权威草稿加入列表。 */
function onPdfCreated(created: LinkBookingApplication): void {
  const existingIndex = applications.value.findIndex((application) => application.id === created.id)
  if (existingIndex >= 0) {
    applications.value.splice(existingIndex, 1, created)
  } else {
    applications.value = [created, ...applications.value]
  }
  successMessage.value = `已从 PDF 人工复核并创建草稿“${created.applicationNumber}”。`
}

/**
 * 七步页面不连接真实 LINK。它会用当前 DRAFT 自动完成七步演示填单并停在 Recap；
 * 业务人员确认 VALIDATE 后，组件返回 Python 已确认的 SUBMITTED 快照。
 */
function openLinkWizard(application: LinkBookingApplication): void {
  wizardApplication.value = application
  showLinkWizard.value = true
}

function onLinkWizardSubmitted(changed: LinkBookingApplication): void {
  const index = applications.value.findIndex((application) => application.id === changed.id)
  if (index >= 0) applications.value.splice(index, 1, changed)
  successMessage.value =
    `模拟 LINK 七步已完成；“${changed.applicationNumber}”已 VALIDATE。full 模式会继续异步发送。`
  wizardApplication.value = null
}

/** 打开确认框而不是直接修改状态，避免误点“提交”或“取消”。 */
function openStatusTransition(
  application: LinkBookingApplication,
  targetStatus: LinkBookingTargetStatus,
): void {
  transitionError.value = ''
  pendingTransition.value = { application, targetStatus }
}

function closeStatusTransition(): void {
  if (!changingApplicationNumber.value) {
    pendingTransition.value = null
    transitionError.value = ''
  }
}

/**
 * 只锁定当前正在变更的那一行。成功后用 Python 返回对象替换旧对象，确保状态、更新时间和
 * 自动生成的订舱参考号都以服务端为准。
 */
async function submitStatusTransition(): Promise<void> {
  const pending = pendingTransition.value
  if (!pending) return

  changingApplicationNumber.value = pending.application.applicationNumber
  transitionError.value = ''
  try {
    const changed = await changeLinkBookingApplicationStatus(
      pending.application.applicationNumber,
      pending.targetStatus,
    )
    const index = applications.value.findIndex((application) => application.id === changed.id)
    if (index >= 0) applications.value.splice(index, 1, changed)

    successMessage.value = `订舱申请“${changed.applicationNumber}”${transitionMeta[pending.targetStatus].successText}`
    pendingTransition.value = null
  } catch (error) {
    transitionError.value =
      error instanceof Error ? error.message : '订舱状态操作失败，请稍后重试'
  } finally {
    changingApplicationNumber.value = ''
  }
}

/**
 * 详情不能直接复用列表行：列表可能已经过期，也没有审计流水。每次打开都向 Java BFF
 * 请求一次，由 Java 再通过 Nacos 调用 Python LINK。
 */
async function openDetail(application: LinkBookingApplication): Promise<void> {
  showDetail.value = true
  detail.value = null
  detailError.value = ''
  carrierRetryError.value = ''
  detailLoading.value = true

  try {
    detail.value = await getLinkBookingApplicationDetail(application.applicationNumber)
    startDetailPolling()
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '读取订舱申请详情失败'
  } finally {
    detailLoading.value = false
  }
}

function closeDetail(): void {
  if (!detailLoading.value) {
    stopDetailPolling()
    showDetail.value = false
    detailError.value = ''
  }
}

function stopDetailPolling(): void {
  if (detailPollTimer) {
    clearInterval(detailPollTimer)
    detailPollTimer = null
  }
}

/** SUBMITTED 是异步状态，详情打开期间定时读取 Python 权威快照。 */
function startDetailPolling(): void {
  stopDetailPolling()
  if (detail.value?.application.status !== 'SUBMITTED') return
  detailPollTimer = setInterval(async () => {
    const applicationNumber = detail.value?.application.applicationNumber
    if (!applicationNumber || detailLoading.value || retryingCarrierSubmission.value) return
    try {
      const refreshed = await getLinkBookingApplicationDetail(applicationNumber)
      detail.value = refreshed
      const index = applications.value.findIndex(
        (application) => application.applicationNumber === applicationNumber,
      )
      if (index >= 0) applications.value.splice(index, 1, refreshed.application)
      if (refreshed.application.status !== 'SUBMITTED') stopDetailPolling()
    } catch {
      // 定时刷新失败不覆盖用户已经看到的详情；手工刷新时仍会显示明确错误。
    }
  }, 3000)
}

async function retryCarrierSubmission(): Promise<void> {
  const current = detail.value
  if (!current?.carrierSubmission?.canRetry) return
  retryingCarrierSubmission.value = true
  carrierRetryError.value = ''
  try {
    current.carrierSubmission = await retryLinkCarrierSubmission(
      current.application.applicationNumber,
    )
    successMessage.value = `已请求重新投递“${current.application.applicationNumber}”。`
    startDetailPolling()
  } catch (error) {
    carrierRetryError.value = error instanceof Error ? error.message : '重新投递失败'
  } finally {
    retryingCarrierSubmission.value = false
  }
}

/**
 * requestedEtd 只有日期、没有时分秒。日期字符串直接替换分隔符可以避免
 * new Date('YYYY-MM-DD') 在不同时区被解释成前一天。
 */
function formatDate(value: string | null): string {
  if (!value) return '—'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.replace(/-/g, '/')
  }
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

/** 审计事件带有时分秒；将 ISO 字符串转换成本地易读格式。 */
function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

onMounted(loadApplications)
onUnmounted(stopDetailPolling)
</script>

<template>
  <section class="link-booking-page">
    <div class="content-actions">
      <p>数据链路：Vue → Java BFF → Nacos → Python LINK → 独立 MySQL。</p>
      <div class="action-group">
        <button class="ghost-button" type="button" :disabled="loading" @click="loadApplications">
          <UiIcon name="refresh" />
          {{ loading ? '正在刷新' : '刷新' }}
        </button>
        <button class="primary-button" type="button" @click="openCreateForm">
          <UiIcon name="plus" />
          新增订舱
        </button>
        <button class="secondary-button" type="button" @click="showPdfImport = true">
          <UiIcon name="upload" />
          从 PDF 导入
        </button>
      </div>
    </div>

    <div v-if="successMessage" class="feedback-banner success" role="status">
      <span>✓</span>
      <p>{{ successMessage }}</p>
    </div>

    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}，请确认 Nacos、Python LINK 和 Java BFF 均已启动。</p>
      <button type="button" @click="loadApplications">重新加载</button>
    </div>

    <div class="panel link-booking-panel">
      <div class="management-toolbar">
        <div>
          <h3>订舱申请列表</h3>
          <p>共 {{ applications.length }} 条申请；新建申请由 Python 初始化为草稿</p>
        </div>
        <div class="toolbar-filters">
          <label class="select-box">
            <select v-model="statusFilter" aria-label="按订舱状态筛选">
              <option value="">全部状态</option>
              <option value="DRAFT">草稿</option>
              <option value="SUBMITTED">已提交</option>
              <option value="CONFIRMED">已确认</option>
              <option value="REJECTED">已拒绝</option>
              <option value="CANCELLED">已取消</option>
            </select>
          </label>
          <label class="search-box">
            <UiIcon name="search" />
            <input v-model="keyword" type="search" placeholder="搜索申请编号或港口" />
          </label>
        </div>
      </div>

      <div v-if="loading" class="empty-state">
        <span class="loading-ring"></span>
        <strong>正在读取订舱申请</strong>
        <p>Java BFF 正在通过 Nacos 调用 Python LINK。</p>
      </div>

      <div v-else-if="filteredApplications.length === 0" class="empty-state">
        <span class="empty-symbol">空</span>
        <strong>{{ keyword || statusFilter ? '没有匹配的订舱申请' : '还没有订舱申请' }}</strong>
        <p>
          {{
            keyword || statusFilter
              ? '请调整搜索或状态筛选条件。'
              : '点击“新增订舱”创建第一份 DEMO 草稿。'
          }}
        </p>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table booking-table">
          <thead>
            <tr>
              <th>申请编号</th>
              <th>航线</th>
              <th>运输 / 箱型</th>
              <th>计划离港</th>
              <th>订舱参考号</th>
              <th>状态</th>
              <th>创建日期</th>
              <th class="table-actions-header">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="application in filteredApplications" :key="application.id">
              <td>
                <div class="booking-number">
                  <strong>{{ application.applicationNumber }}</strong>
                  <small>ID {{ application.id }}</small>
                </div>
              </td>
              <td>
                <div class="booking-route">
                  <span>{{ application.originPort }}</span>
                  <UiIcon name="arrow" />
                  <span>{{ application.destinationPort }}</span>
                </div>
              </td>
              <td><code>{{ application.transportMode }} / {{ application.containerType }}</code></td>
              <td>{{ formatDate(application.requestedEtd) }}</td>
              <td>
                <code v-if="application.bookingReference">
                  {{ application.bookingReference }}
                </code>
                <span v-else class="reference-pending">
                  {{ application.status === 'DRAFT' ? '尚未 VALIDATE' : '等待承运人' }}
                </span>
              </td>
              <td>
                <span
                  class="status-pill booking-status"
                  :class="application.status.toLowerCase()"
                >
                  <i></i>
                  {{ statusLabels[application.status] }}
                </span>
              </td>
              <td>{{ formatDate(application.createdAt) }}</td>
              <td>
                <div class="table-actions">
                  <button
                    class="table-action"
                    type="button"
                    @click="openDetail(application)"
                  >
                    详情
                  </button>
                  <template v-if="application.status === 'DRAFT'">
                    <button
                      class="table-action primary"
                      type="button"
                      @click="openLinkWizard(application)"
                    >
                      模拟 LINK 自动填单
                    </button>
                  </template>
                  <template v-else-if="application.status === 'SUBMITTED'">
                    <span class="action-muted">由模拟承运人异步处理</span>
                  </template>
                  <span v-else class="action-muted">
                    {{
                      application.status === 'CONFIRMED'
                        ? '流程完成'
                        : application.status === 'REJECTED'
                          ? '承运人已拒绝'
                          : '已取消'
                    }}
                  </span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="showCreateForm" class="drawer-backdrop" @click.self="showCreateForm = false">
      <aside class="form-drawer" aria-labelledby="create-link-booking-title">
        <div class="drawer-header">
          <div>
            <h3 id="create-link-booking-title">创建 LINK 订舱草稿</h3>
            <p>仅填写构造学习数据；Python LINK 会把初始状态设为 DRAFT。</p>
          </div>
          <button
            class="close-button"
            type="button"
            aria-label="关闭"
            @click="showCreateForm = false"
          >
            <UiIcon name="close" />
          </button>
        </div>

        <form @submit.prevent="submitApplication">
          <label class="form-field">
            <span>申请编号 <em>必填</em></span>
            <input
              v-model="form.applicationNumber"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 DEMO-BOOKING-0212"
            />
            <small>本地学习环境必须使用 DEMO- 开头的构造编号。</small>
          </label>

          <label class="form-field">
            <span>起运港 <em>必填</em></span>
            <input
              v-model="form.originPort"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 DEMO-SHA"
            />
          </label>

          <label class="form-field">
            <span>目的港 <em>必填</em></span>
            <input
              v-model="form.destinationPort"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 DEMO-LAX"
            />
          </label>

          <label class="form-field">
            <span>运输方式 <em>必填</em></span>
            <select v-model="form.transportMode">
              <option value="SEA">SEA（海运）</option>
              <option value="AIR">AIR（空运）</option>
              <option value="RAIL">RAIL（铁路）</option>
            </select>
          </label>

          <label class="form-field">
            <span>箱型 <em>必填</em></span>
            <select v-model="form.containerType">
              <option value="20GP">20GP</option>
              <option value="40GP">40GP</option>
              <option value="40HQ">40HQ</option>
            </select>
          </label>

          <label class="form-field">
            <span>计划离港日期 <em>必填</em></span>
            <input v-model="form.requestedEtd" type="date" />
            <small>模拟 LINK 自动填单会使用该日期填写日期步骤。</small>
          </label>

          <div v-if="formError" class="inline-error" role="alert">{{ formError }}</div>

          <div class="drawer-actions">
            <button class="secondary-button" type="button" @click="showCreateForm = false">
              取消
            </button>
            <button class="primary-button" type="submit" :disabled="submitting">
              {{ submitting ? '创建中…' : '确认创建' }}
            </button>
          </div>
        </form>
      </aside>
    </div>

    <div v-if="showDetail" class="drawer-backdrop" @click.self="closeDetail">
      <aside class="form-drawer detail-drawer" aria-labelledby="link-booking-detail-title">
        <div class="drawer-header">
          <div>
            <h3 id="link-booking-detail-title">订舱申请详情</h3>
            <p>申请快照与 Python LINK 保存的操作流水。</p>
          </div>
          <button
            class="close-button"
            type="button"
            aria-label="关闭"
            :disabled="detailLoading"
            @click="closeDetail"
          >
            <UiIcon name="close" />
          </button>
        </div>

        <div v-if="detailLoading" class="detail-loading">
          <span class="loading-ring"></span>
          正在通过 Java BFF 读取详情…
        </div>
        <div v-else-if="detailError" class="inline-error" role="alert">
          {{ detailError }}
        </div>
        <template v-else-if="detail">
          <div class="detail-summary">
            <div>
              <span>申请编号</span>
              <strong>{{ detail.application.applicationNumber }}</strong>
            </div>
            <div>
              <span>当前状态</span>
              <strong>{{ statusLabels[detail.application.status] }}</strong>
            </div>
            <div>
              <span>航线</span>
              <strong>{{ detail.application.originPort }} → {{ detail.application.destinationPort }}</strong>
            </div>
            <div>
              <span>运输 / 箱型 / ETD</span>
              <strong>{{ detail.application.transportMode }} · {{ detail.application.containerType }} · {{ formatDate(detail.application.requestedEtd) }}</strong>
            </div>
          </div>

          <section v-if="detail.carrierSubmission" class="carrier-submission-card">
            <div>
              <span>模拟承运人投递</span>
              <strong>{{ detail.carrierSubmission.status }}</strong>
            </div>
            <div>
              <span>承运人请求号</span>
              <strong>{{ detail.carrierSubmission.carrierRequestId || '尚未受理' }}</strong>
            </div>
            <div>
              <span>投递次数</span>
              <strong>{{ detail.carrierSubmission.attemptCount }}</strong>
            </div>
            <p v-if="detail.carrierSubmission.lastErrorMessage">
              {{ detail.carrierSubmission.lastErrorMessage }}
            </p>
            <div v-if="carrierRetryError" class="inline-error" role="alert">
              {{ carrierRetryError }}
            </div>
            <button
              v-if="detail.carrierSubmission.canRetry"
              class="secondary-button"
              type="button"
              :disabled="retryingCarrierSubmission"
              @click="retryCarrierSubmission"
            >
              {{ retryingCarrierSubmission ? '正在重新投递…' : '重新投递' }}
            </button>
            <button
              v-if="detail.carrierSubmission.canCancel"
              class="secondary-button danger-button"
              type="button"
              @click="openStatusTransition(detail.application, 'CANCELLED')"
            >
              取消尚未受理的申请
            </button>
          </section>

          <section class="audit-section" aria-label="操作流水">
            <div class="audit-heading">
              <h4>操作流水</h4>
              <span>{{ detail.events.length }} 条</span>
            </div>
            <ol v-if="detail.events.length" class="audit-timeline">
              <li v-for="event in detail.events" :key="event.id">
                <span class="audit-dot"></span>
                <div>
                  <strong>{{ eventLabels[event.eventType] ?? event.eventType }}</strong>
                  <p>
                    {{ event.previousStatus ? `${statusLabels[event.previousStatus]} → ` : '' }}
                    {{ statusLabels[event.currentStatus] }}
                    <template v-if="event.bookingReference"> · {{ event.bookingReference }}</template>
                  </p>
                  <small>{{ formatDateTime(event.occurredAt) }} · {{ event.actorLabel }}</small>
                </div>
              </li>
            </ol>
            <p v-else class="action-muted">这是迁移前的旧申请，尚未记录操作流水。</p>
          </section>
        </template>
      </aside>
    </div>

    <div
      v-if="pendingTransition && pendingTransitionMeta"
      class="drawer-backdrop"
      @click.self="closeStatusTransition"
    >
      <section
        class="status-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="link-status-transition-title"
      >
        <div class="drawer-header">
          <div>
            <h3 id="link-status-transition-title">{{ pendingTransitionMeta.dialogTitle }}</h3>
            <p>{{ pendingTransition.application.applicationNumber }}</p>
          </div>
          <button
            class="close-button"
            type="button"
            aria-label="关闭"
            :disabled="Boolean(changingApplicationNumber)"
            @click="closeStatusTransition"
          >
            <UiIcon name="close" />
          </button>
        </div>

        <div class="status-transition-content">
          <p>{{ pendingTransitionMeta.description }}</p>
          <div class="transition-flow">
            <span
              class="status-pill booking-status"
              :class="pendingTransition.application.status.toLowerCase()"
            >
              <i></i>{{ statusLabels[pendingTransition.application.status] }}
            </span>
            <UiIcon name="arrow" />
            <span
              class="status-pill booking-status"
              :class="pendingTransition.targetStatus.toLowerCase()"
            >
              <i></i>{{ statusLabels[pendingTransition.targetStatus] }}
            </span>
          </div>
          <div v-if="transitionError" class="inline-error" role="alert">
            {{ transitionError }}
          </div>
        </div>

        <div class="drawer-actions">
          <button
            class="secondary-button"
            type="button"
            :disabled="Boolean(changingApplicationNumber)"
            @click="closeStatusTransition"
          >
            返回
          </button>
          <button
            class="primary-button"
            :class="{ 'danger-button': pendingTransitionMeta.destructive }"
            type="button"
            :disabled="Boolean(changingApplicationNumber)"
            @click="submitStatusTransition"
          >
            {{ changingApplicationNumber ? '处理中…' : pendingTransitionMeta.confirmLabel }}
          </button>
        </div>
      </section>
    </div>

    <PdfImportDrawer v-model="showPdfImport" @created="onPdfCreated" />
    <LinkBookingWizard
      v-model="showLinkWizard"
      :application="wizardApplication"
      @submitted="onLinkWizardSubmitted"
    />
  </section>
</template>
