<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import {
  bindShipmentCaseResource,
  changeShipmentCaseStatus,
  getShipmentCase,
  PlatformApiError,
  type BindShipmentCaseResourceInput,
  type ChangeShipmentCaseStatusInput,
  type ShipmentCase,
  type ShipmentCaseEvent,
  type ShipmentCaseStatus,
  type ShipmentResourceType,
  type ShipmentTransportScenario,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import {
  clearPendingShipmentCaseCommand,
  getOrCreateShipmentCaseCommandKey,
  getPendingShipmentCaseCommand,
} from '@/components/shipment-case/shipmentCasePendingCommands'

const props = defineProps<{
  modelValue: boolean
  caseNumber: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  updated: [shipmentCase: ShipmentCase]
}>()

const detail = ref<ShipmentCase | null>(null)
const loading = ref(false)
const detailError = ref('')
const successMessage = ref('')
const conflictMessage = ref('')

const resourceSubmitting = ref(false)
const resourceError = ref('')
const resourceForm = reactive({
  resourceType: 'LOAD_PLANNING_TASK' as ShipmentResourceType,
  resourceNumber: '',
})
const recoveredResourceCommandNotice = ref('')

const statusSubmitting = ref(false)
const statusError = ref('')
const selectedTargetStatus = ref<ShipmentCaseStatus | null>(null)
const statusReason = ref('')
const recoveredStatusCommandNotice = ref('')

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
  LOAD_PLANNING_TASK: '装载任务',
  LINK_BOOKING_APPLICATION: 'LINK 订舱申请',
}

const eventLabels: Record<string, string> = {
  CASE_CREATED: '创建案件',
  RESOURCE_BOUND: '绑定专业资源',
  CASE_STATUS_CHANGED: '案件状态变更',
}

const allowedStatusTargets = computed<ShipmentCaseStatus[]>(() => {
  if (detail.value?.status === 'DRAFT') return ['ACTIVE', 'CANCELLED']
  if (detail.value?.status === 'ACTIVE') return ['COMPLETED', 'CANCELLED']
  return []
})

const newestEvents = computed<ShipmentCaseEvent[]>(() =>
  [...(detail.value?.events ?? [])].sort((left, right) => right.sequence - left.sequence),
)

const loadPlanningBindingCount = computed(
  () =>
    detail.value?.resourceBindings.filter(
      (binding) => binding.resourceType === 'LOAD_PLANNING_TASK',
    ).length ?? 0,
)

const linkBookingBindingCount = computed(
  () =>
    detail.value?.resourceBindings.filter(
      (binding) => binding.resourceType === 'LINK_BOOKING_APPLICATION',
    ).length ?? 0,
)

watch(
  () => [props.modelValue, props.caseNumber] as const,
  ([open, caseNumber]) => {
    if (open && caseNumber) {
      resetDrawerState()
      void loadDetail(caseNumber)
    }
  },
)

function resetDrawerState(): void {
  detail.value = null
  detailError.value = ''
  successMessage.value = ''
  conflictMessage.value = ''
  resourceError.value = ''
  resourceForm.resourceType = 'LOAD_PLANNING_TASK'
  resourceForm.resourceNumber = ''
  recoveredResourceCommandNotice.value = ''
  statusError.value = ''
  selectedTargetStatus.value = null
  statusReason.value = ''
  recoveredStatusCommandNotice.value = ''
}

function close(): void {
  if (!resourceSubmitting.value && !statusSubmitting.value) {
    emit('update:modelValue', false)
  }
}

async function loadDetail(caseNumber = props.caseNumber): Promise<void> {
  if (!caseNumber) return

  loading.value = true
  detailError.value = ''
  try {
    detail.value = await getShipmentCase(caseNumber)
    restorePendingCommands(detail.value)
    emit('updated', detail.value)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '读取发运案件详情失败'
  } finally {
    loading.value = false
  }
}

/**
 * 409 可能来自旧 revision、资源归属或状态冲突。页面不猜测具体原因，也不自动重放写命令；
 * 它先读取最新案件，再让用户基于新 revision 决定是否提交一条新命令。
 */
async function refreshAfterConflict(message: string): Promise<void> {
  clearPendingCommandsForCase()
  conflictMessage.value = `${message} 已刷新案件最新版本，请确认后重新操作。`

  try {
    const refreshed = await getShipmentCase(props.caseNumber ?? '')
    detail.value = refreshed
    emit('updated', refreshed)
    if (
      selectedTargetStatus.value &&
      !allowedStatusTargets.value.includes(selectedTargetStatus.value)
    ) {
      selectedTargetStatus.value = null
      statusReason.value = ''
    }
  } catch (error) {
    detailError.value =
      error instanceof Error ? error.message : '冲突发生后无法读取案件最新版本'
  }
}

function clearPendingCommandsForCase(): void {
  const caseNumber = props.caseNumber
  if (!caseNumber) return

  clearPendingShipmentCaseCommand('BIND_RESOURCE', caseNumber)
  clearPendingShipmentCaseCommand('CHANGE_STATUS', caseNumber)
  recoveredResourceCommandNotice.value = ''
  recoveredStatusCommandNotice.value = ''
}

/**
 * 详情重新打开或页面刷新后，只恢复仍建立在“当前 revision”上的待确认命令。
 *
 * 如果 revision 已变化，旧命令即使字段文字没变，也不再代表用户当前看到的案件版本；
 * 此时直接清除旧 key，要求用户基于最新事实重新确认。
 */
function restorePendingCommands(shipmentCase: ShipmentCase): void {
  restorePendingResourceCommand(shipmentCase)
  restorePendingStatusCommand(shipmentCase)
}

function restorePendingResourceCommand(shipmentCase: ShipmentCase): void {
  const pending = getPendingShipmentCaseCommand(
    'BIND_RESOURCE',
    shipmentCase.caseNumber,
  )
  if (!pending) return

  try {
    const input = JSON.parse(pending.fingerprint) as Partial<BindShipmentCaseResourceInput>
    const knownResourceType = ['LOAD_PLANNING_TASK', 'LINK_BOOKING_APPLICATION'].includes(
      input.resourceType ?? '',
    )
    const caseAcceptsResources = ['DRAFT', 'ACTIVE'].includes(shipmentCase.status)
    if (
      !knownResourceType ||
      typeof input.resourceNumber !== 'string' ||
      typeof input.expectedRevision !== 'number' ||
      input.expectedRevision !== shipmentCase.revision ||
      !caseAcceptsResources
    ) {
      throw new Error('Stale pending resource command')
    }

    resourceForm.resourceType = input.resourceType as ShipmentResourceType
    resourceForm.resourceNumber = input.resourceNumber
    recoveredResourceCommandNotice.value =
      '已恢复上次结果尚未确认的资源绑定。保持内容不变再次提交，会复用原幂等键。'
  } catch {
    clearPendingShipmentCaseCommand('BIND_RESOURCE', shipmentCase.caseNumber)
    recoveredResourceCommandNotice.value = ''
  }
}

function restorePendingStatusCommand(shipmentCase: ShipmentCase): void {
  const pending = getPendingShipmentCaseCommand(
    'CHANGE_STATUS',
    shipmentCase.caseNumber,
  )
  if (!pending) return

  try {
    const input = JSON.parse(pending.fingerprint) as Partial<ChangeShipmentCaseStatusInput>
    const allowedTargets =
      shipmentCase.status === 'DRAFT'
        ? ['ACTIVE', 'CANCELLED']
        : shipmentCase.status === 'ACTIVE'
          ? ['COMPLETED', 'CANCELLED']
          : []
    if (
      !allowedTargets.includes(input.targetStatus ?? '') ||
      typeof input.expectedRevision !== 'number' ||
      input.expectedRevision !== shipmentCase.revision ||
      typeof input.reason !== 'string'
    ) {
      throw new Error('Stale pending status command')
    }

    selectedTargetStatus.value = input.targetStatus as ShipmentCaseStatus
    statusReason.value = input.reason
    recoveredStatusCommandNotice.value =
      '已恢复上次结果尚未确认的状态命令。保持内容不变再次提交，会复用原幂等键。'
  } catch {
    clearPendingShipmentCaseCommand('CHANGE_STATUS', shipmentCase.caseNumber)
    recoveredStatusCommandNotice.value = ''
  }
}

function validateResource(): string {
  const resourceNumber = resourceForm.resourceNumber.trim()
  if (!resourceNumber) return '请输入待绑定的资源编号。'
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(resourceNumber)) {
    return '资源编号只能包含字母、数字、下划线和短横线。'
  }
  if (resourceNumber.length > 64) return '资源编号不能超过 64 个字符。'
  return ''
}

function resourceCommandKey(input: BindShipmentCaseResourceInput): string {
  const fingerprint = JSON.stringify(input)
  return getOrCreateShipmentCaseCommandKey({
    commandType: 'BIND_RESOURCE',
    scope: detail.value?.caseNumber ?? props.caseNumber ?? '',
    fingerprint,
    keyPrefix: 'shipment-resource-bind',
  })
}

function discardPendingResourceCommand(): void {
  const caseNumber = detail.value?.caseNumber ?? props.caseNumber
  if (!caseNumber) return

  clearPendingShipmentCaseCommand('BIND_RESOURCE', caseNumber)
  recoveredResourceCommandNotice.value = ''
}

async function bindResource(): Promise<void> {
  if (!detail.value) return
  resourceError.value = validateResource()
  successMessage.value = ''
  conflictMessage.value = ''
  if (resourceError.value) return

  const input: BindShipmentCaseResourceInput = {
    resourceType: resourceForm.resourceType,
    resourceNumber: resourceForm.resourceNumber.trim(),
    expectedRevision: detail.value.revision,
  }
  const fingerprint = JSON.stringify(input)
  const idempotencyKey = resourceCommandKey(input)
  resourceSubmitting.value = true

  try {
    const changed = await bindShipmentCaseResource(
      detail.value.caseNumber,
      input,
      idempotencyKey,
    )
    detail.value = changed
    clearPendingShipmentCaseCommand(
      'BIND_RESOURCE',
      changed.caseNumber,
      fingerprint,
    )
    recoveredResourceCommandNotice.value = ''
    resourceForm.resourceNumber = ''
    successMessage.value = `${resourceLabels[input.resourceType]}“${input.resourceNumber}”已绑定。`
    emit('updated', changed)
  } catch (error) {
    const message = error instanceof Error ? error.message : '绑定专业资源失败'
    if (error instanceof PlatformApiError && error.status === 409) {
      resourceError.value = ''
      await refreshAfterConflict(message)
    } else {
      resourceError.value = message
      if (error instanceof PlatformApiError && error.status === 400) {
        clearPendingShipmentCaseCommand(
          'BIND_RESOURCE',
          detail.value.caseNumber,
          fingerprint,
        )
        recoveredResourceCommandNotice.value = ''
      }
    }
  } finally {
    resourceSubmitting.value = false
  }
}

function selectStatusTarget(targetStatus: ShipmentCaseStatus): void {
  discardPendingStatusCommand()
  selectedTargetStatus.value = targetStatus
  statusReason.value = ''
  statusError.value = ''
  conflictMessage.value = ''
}

function cancelStatusChange(): void {
  if (!statusSubmitting.value) {
    discardPendingStatusCommand()
    selectedTargetStatus.value = null
    statusReason.value = ''
    statusError.value = ''
  }
}

function statusCommandKey(input: ChangeShipmentCaseStatusInput): string {
  const fingerprint = JSON.stringify(input)
  return getOrCreateShipmentCaseCommandKey({
    commandType: 'CHANGE_STATUS',
    scope: detail.value?.caseNumber ?? props.caseNumber ?? '',
    fingerprint,
    keyPrefix: 'shipment-status-change',
  })
}

function discardPendingStatusCommand(): void {
  const caseNumber = detail.value?.caseNumber ?? props.caseNumber
  if (!caseNumber) return

  clearPendingShipmentCaseCommand('CHANGE_STATUS', caseNumber)
  recoveredStatusCommandNotice.value = ''
}

async function changeStatus(): Promise<void> {
  if (!detail.value || !selectedTargetStatus.value) return

  const reason = statusReason.value.trim()
  if (!reason) {
    statusError.value = '请输入本次状态变化的业务原因。'
    return
  }
  if (reason.length > 2000) {
    statusError.value = '状态变化原因不能超过 2000 个字符。'
    return
  }

  const input: ChangeShipmentCaseStatusInput = {
    targetStatus: selectedTargetStatus.value,
    expectedRevision: detail.value.revision,
    reason,
  }
  const fingerprint = JSON.stringify(input)
  const idempotencyKey = statusCommandKey(input)
  statusSubmitting.value = true
  statusError.value = ''
  successMessage.value = ''
  conflictMessage.value = ''

  try {
    const changed = await changeShipmentCaseStatus(
      detail.value.caseNumber,
      input,
      idempotencyKey,
    )
    detail.value = changed
    clearPendingShipmentCaseCommand(
      'CHANGE_STATUS',
      changed.caseNumber,
      fingerprint,
    )
    recoveredStatusCommandNotice.value = ''
    selectedTargetStatus.value = null
    statusReason.value = ''
    successMessage.value = `案件状态已更新为“${statusLabels[changed.status]}”。`
    emit('updated', changed)
  } catch (error) {
    const message = error instanceof Error ? error.message : '更新发运案件状态失败'
    if (error instanceof PlatformApiError && error.status === 409) {
      statusError.value = ''
      await refreshAfterConflict(message)
    } else {
      statusError.value = message
      if (error instanceof PlatformApiError && error.status === 400) {
        clearPendingShipmentCaseCommand(
          'CHANGE_STATUS',
          detail.value.caseNumber,
          fingerprint,
        )
        recoveredStatusCommandNotice.value = ''
      }
    }
  } finally {
    statusSubmitting.value = false
  }
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

function verificationSourceLabel(value: string): string {
  const labels: Record<string, string> = {
    LOCAL_LOAD_PLANNING_DATABASE: 'Agent Runtime 本地数据库',
    LINK_BOOKING_HTTP: 'LINK 只读接口',
  }
  return labels[value] ?? value
}

function resourceWorkspacePath(resourceType: ShipmentResourceType): string {
  return resourceType === 'LOAD_PLANNING_TASK'
    ? '/supply-chain/load-planning/tasks'
    : '/link-booking/applications'
}

function eventTitle(event: ShipmentCaseEvent): string {
  return eventLabels[event.eventType] ?? event.eventType
}
</script>

<template>
  <div v-if="modelValue" class="drawer-backdrop" @click.self="close">
    <aside class="form-drawer shipment-detail-drawer" aria-labelledby="shipment-detail-title">
      <div class="drawer-header shipment-detail-header">
        <div>
          <span class="shipment-drawer-kicker">SHIPMENT CASE</span>
          <h3 id="shipment-detail-title">发运案件详情</h3>
          <p>统一查看案件快照、专业资源、生命周期操作与审计记录。</p>
        </div>
        <div class="shipment-detail-header-actions">
          <button
            class="shipment-icon-button"
            type="button"
            aria-label="刷新案件详情"
            :disabled="loading"
            @click="loadDetail()"
          >
            <UiIcon name="refresh" />
          </button>
          <button
            class="close-button"
            type="button"
            aria-label="关闭"
            :disabled="resourceSubmitting || statusSubmitting"
            @click="close"
          >
            <UiIcon name="close" />
          </button>
        </div>
      </div>

      <div v-if="loading && !detail" class="detail-loading">
        <span class="loading-ring"></span>
        正在读取案件最新版本
      </div>

      <div v-else-if="detailError && !detail" class="shipment-detail-failed">
        <span>!</span>
        <strong>案件详情读取失败</strong>
        <p>{{ detailError }}</p>
        <button class="secondary-button" type="button" @click="loadDetail()">重新加载</button>
      </div>

      <template v-else-if="detail">
        <div v-if="successMessage" class="feedback-banner success" role="status">
          <span>✓</span>
          <p>{{ successMessage }}</p>
        </div>
        <div v-if="conflictMessage" class="feedback-banner warning" role="status">
          <span>!</span>
          <p>{{ conflictMessage }}</p>
        </div>
        <div v-if="detailError" class="feedback-banner error" role="alert">
          <span>!</span>
          <p>{{ detailError }}</p>
          <button type="button" @click="loadDetail()">重新加载</button>
        </div>

        <section class="shipment-case-identity">
          <div>
            <span>{{ detail.caseNumber }}</span>
            <h4>{{ detail.caseName }}</h4>
            <p>{{ detail.description || '暂无业务说明' }}</p>
          </div>
          <span class="shipment-status-pill" :data-status="detail.status">
            <i></i>
            {{ statusLabels[detail.status] }}
          </span>
        </section>

        <section class="shipment-overview-grid" aria-label="案件概要">
          <article>
            <span>运输场景</span>
            <strong>{{ scenarioLabels[detail.transportScenario] }}</strong>
            <small>{{ detail.transportScenario }}</small>
          </article>
          <article>
            <span>负责组织</span>
            <strong>{{ detail.responsibleOrganizationCode }}</strong>
            <small>当前为服务端演示占位组织</small>
          </article>
          <article>
            <span>当前修订号</span>
            <strong>R{{ detail.revision }}</strong>
            <small>写操作将以此版本校验</small>
          </article>
          <article>
            <span>专业资源</span>
            <strong>{{ detail.resourceBindings.length }}</strong>
            <small>{{ loadPlanningBindingCount }} 装载 · {{ linkBookingBindingCount }} 订舱</small>
          </article>
        </section>

        <div class="shipment-date-strip">
          <span><UiIcon name="clock" />创建 {{ formatDateTime(detail.createdAt) }}</span>
          <span><UiIcon name="refresh" />更新 {{ formatDateTime(detail.updatedAt) }}</span>
        </div>

        <section class="shipment-detail-section">
          <div class="shipment-section-heading">
            <div>
              <span>PROFESSIONAL RESOURCES</span>
              <h4>专业资源绑定</h4>
              <p>案件只保存稳定编号和验证快照，业务明细仍由专业模块负责。</p>
            </div>
            <strong>{{ detail.resourceBindings.length }} 项</strong>
          </div>

          <div v-if="detail.resourceBindings.length" class="shipment-resource-list">
            <article
              v-for="binding in detail.resourceBindings"
              :key="binding.id"
              class="shipment-resource-card"
            >
              <span class="shipment-resource-icon">
                <UiIcon :name="binding.resourceType === 'LOAD_PLANNING_TASK' ? 'box' : 'database'" />
              </span>
              <div class="shipment-resource-main">
                <div>
                  <span>{{ resourceLabels[binding.resourceType] }}</span>
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
                  <div>
                    <dt>绑定时间</dt>
                    <dd>{{ formatDateTime(binding.boundAt) }}</dd>
                  </div>
                </dl>
              </div>
              <RouterLink
                class="shipment-resource-link"
                :to="resourceWorkspacePath(binding.resourceType)"
              >
                打开工作台
                <UiIcon name="arrow" />
              </RouterLink>
            </article>
          </div>
          <div v-else class="shipment-section-empty">
            <UiIcon name="layers" />
            <div>
              <strong>尚未绑定专业资源</strong>
              <p>可关联已有装载任务或 LINK 订舱申请，形成同一票发运的统一时间线。</p>
            </div>
          </div>

          <form
            v-if="allowedStatusTargets.length"
            class="shipment-resource-bind-form"
            @submit.prevent="bindResource"
          >
            <div class="shipment-inline-form-heading">
              <div>
                <strong>绑定已有资源</strong>
                <span>提交前会由 Agent Runtime 验证资源是否存在</span>
              </div>
              <span>当前 R{{ detail.revision }}</span>
            </div>
            <div class="shipment-resource-fields">
              <label class="form-field">
                <span>资源类型</span>
                <select
                  v-model="resourceForm.resourceType"
                  @change="discardPendingResourceCommand"
                >
                  <option value="LOAD_PLANNING_TASK">装载任务</option>
                  <option value="LINK_BOOKING_APPLICATION">LINK 订舱申请</option>
                </select>
              </label>
              <label class="form-field">
                <span>资源编号</span>
                <input
                  v-model="resourceForm.resourceNumber"
                  maxlength="64"
                  :placeholder="
                    resourceForm.resourceType === 'LOAD_PLANNING_TASK'
                      ? '例如 LP-DEMO-0001'
                      : '例如 DEMO-BOOKING-0001'
                  "
                  @input="discardPendingResourceCommand"
                />
              </label>
              <button class="primary-button" type="submit" :disabled="resourceSubmitting">
                {{ resourceSubmitting ? '正在验证并绑定…' : '验证并绑定' }}
              </button>
            </div>
            <div v-if="resourceError" class="inline-error" role="alert">
              {{ resourceError }}
            </div>
            <div
              v-if="recoveredResourceCommandNotice"
              class="feedback-banner warning"
              role="status"
            >
              <span>!</span>
              <p>{{ recoveredResourceCommandNotice }}</p>
            </div>
          </form>
        </section>

        <section class="shipment-detail-section">
          <div class="shipment-section-heading">
            <div>
              <span>LIFECYCLE</span>
              <h4>案件生命周期</h4>
              <p>状态变化必须填写原因，并由 Python 状态机再次校验。</p>
            </div>
            <span class="shipment-status-pill compact" :data-status="detail.status">
              <i></i>{{ statusLabels[detail.status] }}
            </span>
          </div>

          <div v-if="allowedStatusTargets.length" class="shipment-status-actions">
            <button
              v-for="target in allowedStatusTargets"
              :key="target"
              class="shipment-status-action"
              :class="{ danger: target === 'CANCELLED' }"
              type="button"
              @click="selectStatusTarget(target)"
            >
              <UiIcon :name="target === 'CANCELLED' ? 'close' : 'check'" />
              <span>
                <strong>
                  {{
                    target === 'ACTIVE'
                      ? '激活案件'
                      : target === 'COMPLETED'
                        ? '完成案件'
                        : '取消案件'
                  }}
                </strong>
                <small>
                  {{
                    target === 'ACTIVE'
                      ? '开始并行推进专业作业'
                      : target === 'COMPLETED'
                        ? '确认本票计划内工作已经结束'
                        : '终止本票案件，取消后不可恢复'
                  }}
                </small>
              </span>
              <UiIcon name="arrow" />
            </button>
          </div>
          <div v-else class="shipment-terminal-state">
            <UiIcon :name="detail.status === 'COMPLETED' ? 'check' : 'shield'" />
            <div>
              <strong>案件已进入终态</strong>
              <p>
                {{
                  detail.status === 'COMPLETED'
                    ? '该案件已完成，当前页面保留只读快照和审计记录。'
                    : '该案件已取消，不能通过普通状态命令重新激活。'
                }}
              </p>
            </div>
          </div>

          <form
            v-if="selectedTargetStatus"
            class="shipment-status-form"
            @submit.prevent="changeStatus"
          >
            <div class="shipment-status-transition">
              <span class="shipment-status-pill compact" :data-status="detail.status">
                <i></i>{{ statusLabels[detail.status] }}
              </span>
              <UiIcon name="arrow" />
              <span
                class="shipment-status-pill compact"
                :data-status="selectedTargetStatus"
              >
                <i></i>{{ statusLabels[selectedTargetStatus] }}
              </span>
            </div>
            <label class="form-field">
              <span>状态变化原因 <em>必填</em></span>
              <textarea
                v-model="statusReason"
                maxlength="2000"
                rows="4"
                placeholder="说明本次激活、完成或取消的业务依据"
                @input="discardPendingStatusCommand"
              ></textarea>
            </label>
            <div v-if="statusError" class="inline-error" role="alert">{{ statusError }}</div>
            <div
              v-if="recoveredStatusCommandNotice"
              class="feedback-banner warning"
              role="status"
            >
              <span>!</span>
              <p>{{ recoveredStatusCommandNotice }}</p>
            </div>
            <div class="shipment-status-form-actions">
              <button
                class="secondary-button"
                type="button"
                :disabled="statusSubmitting"
                @click="cancelStatusChange"
              >
                返回
              </button>
              <button
                class="primary-button"
                :class="{ 'danger-button': selectedTargetStatus === 'CANCELLED' }"
                type="submit"
                :disabled="statusSubmitting"
              >
                {{ statusSubmitting ? '正在提交…' : '确认状态变化' }}
              </button>
            </div>
          </form>
        </section>

        <section class="shipment-detail-section">
          <div class="shipment-section-heading">
            <div>
              <span>AUDIT TRAIL</span>
              <h4>案件时间线</h4>
              <p>
                事件按最新优先显示，每条成功命令对应一个不可变 sequence；当前
                SYSTEM_DEMO 仍是演示占位身份。
              </p>
            </div>
            <strong>{{ detail.events.length }} 条</strong>
          </div>

          <ol v-if="newestEvents.length" class="shipment-audit-timeline">
            <li v-for="event in newestEvents" :key="event.id">
              <span class="shipment-audit-sequence">{{ event.sequence }}</span>
              <div class="shipment-audit-card">
                <div class="shipment-audit-card-heading">
                  <div>
                    <strong>{{ eventTitle(event) }}</strong>
                    <span>{{ formatDateTime(event.occurredAt) }}</span>
                  </div>
                  <span v-if="event.resourceType" class="shipment-audit-resource">
                    {{ resourceLabels[event.resourceType] }}
                  </span>
                </div>
                <p>{{ event.detail }}</p>
                <div class="shipment-audit-meta">
                  <span>{{ event.actorDisplayName }} · {{ event.actorSource }}</span>
                  <code>{{ event.requestId }}</code>
                </div>
              </div>
            </li>
          </ol>
        </section>
      </template>
    </aside>
  </div>
</template>
