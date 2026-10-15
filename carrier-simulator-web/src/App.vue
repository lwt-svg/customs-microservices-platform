<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  getBooking,
  getBookings,
  getCallbackDeliveries,
  makeDecision,
  retryCallbackDelivery,
  startProcessing,
  type CallbackDelivery,
  type CarrierBooking,
  type CarrierBookingStatus,
} from '@/api/carrier'

type TabKey = 'bookings' | 'callbacks'
type DecisionMode = 'confirm' | 'reject' | null

const currentTab = ref<TabKey>('bookings')
const bookings = ref<CarrierBooking[]>([])
const callbacks = ref<CallbackDelivery[]>([])
const statusFilter = ref<CarrierBookingStatus | ''>('')
const keyword = ref('')
const selectedBooking = ref<CarrierBooking | null>(null)
const loading = ref(false)
const callbackLoading = ref(false)
const actionLoading = ref(false)
const retryingId = ref<number | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
const decisionMode = ref<DecisionMode>(null)
const decisionValue = ref('')

const selectedIsOpen = computed(
  () => selectedBooking.value?.status === 'RECEIVED' || selectedBooking.value?.status === 'PROCESSING',
)

function statusText(status: CarrierBookingStatus | CallbackDelivery['status']): string {
  const labels: Record<CarrierBookingStatus | CallbackDelivery['status'], string> = {
    RECEIVED: '已接收',
    PROCESSING: '处理中',
    CONFIRMED: '已确认',
    REJECTED: '已拒绝',
    PENDING: '待投递',
    SENDING: '投递中',
    RETRY_WAIT: '等待重试',
    DELIVERED: '投递成功',
    DEAD: '投递终止',
  }
  return labels[status]
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function clearFeedback(): void {
  errorMessage.value = ''
  successMessage.value = ''
}

async function loadBookings(keepSelection = false): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    bookings.value = await getBookings({ status: statusFilter.value, keyword: keyword.value })
    if (keepSelection && selectedBooking.value) {
      selectedBooking.value = await getBooking(selectedBooking.value.id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取模拟订舱列表失败'
  } finally {
    loading.value = false
  }
}

async function loadCallbacks(): Promise<void> {
  callbackLoading.value = true
  errorMessage.value = ''
  try {
    callbacks.value = await getCallbackDeliveries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取回调投递记录失败'
  } finally {
    callbackLoading.value = false
  }
}

async function selectBooking(id: number): Promise<void> {
  clearFeedback()
  actionLoading.value = true
  try {
    selectedBooking.value = await getBooking(id)
    decisionMode.value = null
    decisionValue.value = ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取订舱详情失败'
  } finally {
    actionLoading.value = false
  }
}

async function processBooking(): Promise<void> {
  if (!selectedBooking.value) return
  clearFeedback()
  actionLoading.value = true
  try {
    selectedBooking.value = await startProcessing(selectedBooking.value.id)
    successMessage.value = '已开始处理该模拟订舱。下一步可确认或拒绝。'
    await loadBookings(false)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '开始处理失败'
  } finally {
    actionLoading.value = false
  }
}

function openDecision(mode: Exclude<DecisionMode, null>): void {
  clearFeedback()
  decisionMode.value = mode
  // 确认编号由服务端统一生成；拒绝时则保存用户填写的构造原因。
  decisionValue.value = ''
}

function closeDecision(): void {
  decisionMode.value = null
  decisionValue.value = ''
}

async function submitDecision(): Promise<void> {
  if (!selectedBooking.value || !decisionMode.value) return
  if (decisionMode.value === 'reject' && !decisionValue.value.trim()) {
    errorMessage.value = '拒绝时必须填写原因，方便平台侧和学习笔记追踪。'
    return
  }

  clearFeedback()
  actionLoading.value = true
  try {
    selectedBooking.value =
      decisionMode.value === 'confirm'
        ? await makeDecision(selectedBooking.value.id, {
            decision: 'CONFIRMED',
          })
        : await makeDecision(selectedBooking.value.id, {
            decision: 'REJECTED',
            rejectionReasonCode: decisionValue.value.trim(),
          })
    successMessage.value = decisionMode.value === 'confirm' ? '模拟订舱已确认，并已创建回调投递任务。' : '模拟订舱已拒绝，并已创建回调投递任务。'
    closeDecision()
    await Promise.all([loadBookings(false), loadCallbacks()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交决定失败'
  } finally {
    actionLoading.value = false
  }
}

async function retryCallback(id: number): Promise<void> {
  clearFeedback()
  retryingId.value = id
  try {
    await retryCallbackDelivery(id)
    successMessage.value = '已请求重新投递回调；结果请查看该行状态和尝试次数。'
    await loadCallbacks()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回调重试失败'
  } finally {
    retryingId.value = null
  }
}

function switchTab(tab: TabKey): void {
  currentTab.value = tab
  clearFeedback()
  if (tab === 'callbacks') void loadCallbacks()
}

async function refreshAll(): Promise<void> {
  await Promise.all([loadBookings(true), loadCallbacks()])
}

onMounted(async () => {
  await Promise.all([loadBookings(), loadCallbacks()])
})
</script>

<template>
  <main class="portal-shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">C</div>
        <div>
          <p class="eyebrow">DEMO CARRIER</p>
          <h1>本地模拟承运人门户</h1>
        </div>
      </div>
      <div class="local-notice">
        <span class="environment-dot"></span>
        仅使用 DEMO 构造数据 · 服务地址 127.0.0.1:8100
      </div>
    </header>

    <section class="page-head">
      <div>
        <p class="eyebrow">第 22 章 · 独立门户</p>
        <h2>模拟订舱处理台</h2>
        <p>这里模拟承运人接收、处理、确认/拒绝订舱，以及向 LINK 平台回调结果的过程。</p>
      </div>
      <button class="button secondary" :disabled="loading || callbackLoading" @click="refreshAll">
        刷新数据
      </button>
    </section>

    <div v-if="errorMessage" class="feedback error" role="alert">
      {{ errorMessage }}
      <button type="button" aria-label="关闭错误提示" @click="errorMessage = ''">×</button>
    </div>
    <div v-if="successMessage" class="feedback success" role="status">
      {{ successMessage }}
      <button type="button" aria-label="关闭成功提示" @click="successMessage = ''">×</button>
    </div>

    <nav class="tabs" aria-label="门户页面">
      <button :class="{ active: currentTab === 'bookings' }" @click="switchTab('bookings')">
        模拟订舱
        <span>{{ bookings.length }}</span>
      </button>
      <button :class="{ active: currentTab === 'callbacks' }" @click="switchTab('callbacks')">
        回调投递
        <span>{{ callbacks.filter((item) => item.status === 'RETRY_WAIT' || item.status === 'DEAD').length }}</span>
      </button>
    </nav>

    <section v-if="currentTab === 'bookings'" class="booking-layout">
      <div class="list-card">
        <div class="filter-bar">
          <label>
            <span>状态</span>
            <select v-model="statusFilter" @change="loadBookings()">
              <option value="">全部状态</option>
              <option value="RECEIVED">已接收</option>
              <option value="PROCESSING">处理中</option>
              <option value="CONFIRMED">已确认</option>
              <option value="REJECTED">已拒绝</option>
            </select>
          </label>
          <label class="search-field">
            <span>搜索</span>
            <input v-model="keyword" placeholder="申请号或承运人请求号" @keyup.enter="loadBookings()" />
          </label>
          <button class="button secondary" :disabled="loading" @click="loadBookings()">查询</button>
        </div>

        <div v-if="loading" class="empty-state">正在读取模拟订舱…</div>
        <div v-else-if="bookings.length === 0" class="empty-state">没有符合当前筛选条件的模拟订舱。</div>
        <div v-else class="booking-list">
          <button
            v-for="booking in bookings"
            :key="booking.id"
            class="booking-row"
            :class="{ selected: selectedBooking?.id === booking.id }"
            @click="selectBooking(booking.id)"
          >
            <span>
              <strong>{{ booking.applicationNumber }}</strong>
              <small>{{ booking.carrierRequestId }} · {{ booking.originPort }} → {{ booking.destinationPort }}</small>
            </span>
            <span class="status-pill" :class="booking.status.toLowerCase()">{{ statusText(booking.status) }}</span>
          </button>
        </div>
      </div>

      <aside class="detail-card">
        <div v-if="!selectedBooking" class="detail-placeholder">
          <span class="placeholder-icon">↗</span>
          <strong>选择一条模拟订舱</strong>
          <p>在左侧查看请求详情、处理时间线和可执行操作。</p>
        </div>
        <template v-else>
          <div class="detail-header">
            <div>
              <p class="eyebrow">承运人请求号</p>
              <h3>{{ selectedBooking.carrierRequestId }}</h3>
            </div>
            <span class="status-pill" :class="selectedBooking.status.toLowerCase()">{{ statusText(selectedBooking.status) }}</span>
          </div>
          <dl class="field-grid">
            <div><dt>LINK 申请号</dt><dd>{{ selectedBooking.applicationNumber }}</dd></div>
            <div><dt>运输方式</dt><dd>{{ selectedBooking.transportMode }}</dd></div>
            <div><dt>航线</dt><dd>{{ selectedBooking.originPort }} → {{ selectedBooking.destinationPort }}</dd></div>
            <div><dt>箱型</dt><dd>{{ selectedBooking.containerType }}</dd></div>
            <div><dt>期望 ETD</dt><dd>{{ selectedBooking.requestedEtd || '—' }}</dd></div>
            <div><dt>订舱参考号</dt><dd>{{ selectedBooking.bookingReference || '尚未生成' }}</dd></div>
            <div v-if="selectedBooking.rejectionReasonCode" class="span-all"><dt>拒绝原因代码</dt><dd class="danger-text">{{ selectedBooking.rejectionReasonCode }}</dd></div>
          </dl>

          <div v-if="selectedIsOpen" class="actions">
            <button
              v-if="selectedBooking.status === 'RECEIVED'"
              class="button secondary"
              :disabled="actionLoading"
              @click="processBooking"
            >
              {{ actionLoading ? '处理中…' : '开始处理' }}
            </button>
            <template v-else>
              <button class="button primary" :disabled="actionLoading" @click="openDecision('confirm')">确认订舱</button>
              <button class="button danger" :disabled="actionLoading" @click="openDecision('reject')">拒绝订舱</button>
            </template>
          </div>
          <p v-else class="terminal-tip">该请求已进入终态；回调投递结果请在“回调投递”页签查看。</p>

          <section class="timeline-section">
            <h4>处理时间线</h4>
            <ol class="timeline">
              <li v-for="event in selectedBooking.timeline" :key="`${event.eventType}-${event.occurredAt}`">
                <span class="timeline-dot"></span>
                <div><strong>{{ event.description }}</strong><small>{{ event.eventType }} · {{ formatTime(event.occurredAt) }}</small></div>
              </li>
            </ol>
          </section>
        </template>
      </aside>
    </section>

    <section v-else class="callback-card">
      <div class="card-head">
        <div>
          <h3>回调投递记录</h3>
          <p>确认或拒绝后会尝试把结果回调给 LINK 服务。失败记录可以在此显式重试。</p>
        </div>
        <button class="button secondary" :disabled="callbackLoading" @click="loadCallbacks()">刷新列表</button>
      </div>
      <div v-if="callbackLoading" class="empty-state">正在读取回调投递记录…</div>
      <div v-else-if="callbacks.length === 0" class="empty-state">暂时还没有回调投递记录。</div>
      <div v-else class="callback-table-wrap">
        <table>
          <thead>
            <tr><th>回调编号</th><th>事件</th><th>状态</th><th>尝试次数</th><th>最后错误</th><th>时间</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="delivery in callbacks" :key="delivery.id">
              <td>{{ delivery.callbackId }}</td>
              <td>{{ delivery.eventType }}</td>
              <td><span class="status-pill" :class="delivery.status.toLowerCase()">{{ statusText(delivery.status) }}</span></td>
              <td>{{ delivery.attemptCount }}</td>
              <td class="error-cell">{{ delivery.lastError || '—' }}</td>
              <td>{{ formatTime(delivery.deliveredAt || delivery.createdAt) }}</td>
              <td>
                <button
                  v-if="delivery.status === 'RETRY_WAIT' || delivery.status === 'DEAD'"
                  class="text-button"
                  :disabled="retryingId === delivery.id"
                  @click="retryCallback(delivery.id)"
                >
                  {{ retryingId === delivery.id ? '重试中…' : '重新投递' }}
                </button>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div v-if="decisionMode" class="modal-backdrop" @click.self="closeDecision">
      <section class="decision-modal" role="dialog" aria-modal="true" :aria-label="decisionMode === 'confirm' ? '确认订舱' : '拒绝订舱'">
        <p class="eyebrow">模拟承运人决定</p>
        <h3>{{ decisionMode === 'confirm' ? '确认这条订舱？' : '拒绝这条订舱？' }}</h3>
        <p v-if="decisionMode === 'confirm'">服务端会生成 DEMO- 前缀的构造订舱参考号，并创建回调投递任务。</p>
        <p v-else>拒绝原因将随回调发送给 LINK 平台，因此必须清晰、可追踪。</p>
        <label class="modal-field">
          <span>{{ decisionMode === 'confirm' ? '订舱参考号由服务端生成' : '拒绝原因（必填）' }}</span>
          <input
            v-model="decisionValue"
            :disabled="decisionMode === 'confirm'"
            :placeholder="decisionMode === 'confirm' ? '确认后自动生成 DEMO-BOOK- 编号' : '例如：DEMO 舱位不足'"
            @keyup.enter="submitDecision"
          />
        </label>
        <div class="modal-actions">
          <button class="button secondary" :disabled="actionLoading" @click="closeDecision">取消</button>
          <button class="button" :class="decisionMode === 'confirm' ? 'primary' : 'danger'" :disabled="actionLoading" @click="submitDecision">
            {{ actionLoading ? '提交中…' : decisionMode === 'confirm' ? '确认并投递回调' : '拒绝并投递回调' }}
          </button>
        </div>
      </section>
    </div>
  </main>
</template>
