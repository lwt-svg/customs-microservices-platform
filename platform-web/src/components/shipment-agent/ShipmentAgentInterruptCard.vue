<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type {
  ResumeShipmentAgentRunInput,
  ShipmentAgentAction,
  ShipmentAgentCapability,
  ShipmentAgentDataFact,
  ShipmentAgentRequestedFact,
  ShipmentAgentRun,
  ShipmentDataFactSourceType,
  ShipmentDataReadinessIssue,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  run: ShipmentAgentRun
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  submit: [payload: ResumeShipmentAgentRunInput]
}>()

const comment = ref('')
const selectedResourceNumber = ref('')
const requiredCapabilities = ref<ShipmentAgentCapability[]>([])
const factDrafts = ref<Record<string, string>>({})
const dataSourceType = ref<ShipmentDataFactSourceType>('MANUAL_CONFIRMATION')
const sourceReference = ref('')
const sourceLocator = ref('')
const observedAtLocal = ref('')
const expiresAtLocal = ref('')
const localError = ref('')

const interrupt = computed(() => props.run.pendingInterrupt)
const interruptKey = computed(
  () =>
    `${props.run.runId}:${props.run.runVersion}:${props.run.pendingInterrupt?.type ?? 'NONE'}`,
)

const capabilityOptions: Array<{
  value: ShipmentAgentCapability
  label: string
  description: string
}> = [
  {
    value: 'LOAD_PLANNING',
    label: '智能装载',
    description: '生成装载方案，并完成 3D 复核、审批和正式发布。',
  },
  {
    value: 'LINK_BOOKING',
    label: 'LINK 订舱',
    description: '协调七步填单、提交审批和承运人回调。',
  },
]

const resourceCandidates = computed<string[]>(() => {
  const details = interrupt.value?.details ?? {}
  const values =
    details.candidates ??
    details.resourceCandidates ??
    details.resource_candidates ??
    details.availableResources ??
    details.available_resources
  return Array.isArray(values)
    ? values.filter((value): value is string => typeof value === 'string')
    : []
})

const resourceTypeLabel = computed(() => {
  const details = interrupt.value?.details ?? {}
  const value = details.resourceType ?? details.resource_type
  if (value === 'LOAD_PLANNING_TASK') return '装载任务'
  if (value === 'LINK_BOOKING_APPLICATION') return 'LINK 订舱申请'
  return '专业资源'
})

const isScenarioPolicy = computed(
  () => interrupt.value?.type === 'SCENARIO_POLICY_CONFIRMATION',
)
const isResourceSelection = computed(
  () => interrupt.value?.type === 'RESOURCE_SELECTION',
)
const isDataReadiness = computed(
  () => interrupt.value?.type === 'DATA_READINESS_REMEDIATION',
)

function detailValue(...keys: string[]): unknown {
  const details = interrupt.value?.details ?? {}
  for (const key of keys) {
    if (key in details) return details[key]
  }
  return undefined
}

function normalizeRequestedFact(value: unknown): ShipmentAgentRequestedFact | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const subjectType = record.subjectType ?? record.subject_type
  const subjectCode = record.subjectCode ?? record.subject_code
  const factType = record.factType ?? record.fact_type
  if (
    !['CARGO_ITEM', 'PALLET_SPEC'].includes(String(subjectType)) ||
    !String(subjectCode ?? '').trim() ||
    !['AVAILABLE_QUANTITY', 'MASTER_DATA_CONFIRMED'].includes(String(factType))
  ) {
    return null
  }
  const valueType = record.valueType ?? record.value_type
  return {
    subjectType: subjectType as ShipmentAgentRequestedFact['subjectType'],
    subjectCode: String(subjectCode),
    factType: factType as ShipmentAgentRequestedFact['factType'],
    label: typeof record.label === 'string' ? record.label : null,
    reason: typeof record.reason === 'string' ? record.reason : null,
    valueType: ['NUMBER', 'INTEGER', 'BOOLEAN', 'TEXT'].includes(String(valueType))
      ? (valueType as ShipmentAgentRequestedFact['valueType'])
      : null,
    unit: typeof record.unit === 'string' ? record.unit : null,
    required: record.required !== false,
  }
}

function normalizeIssue(value: unknown): ShipmentDataReadinessIssue | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const issueId = record.issueId ?? record.issue_id
  const issueCode = record.issueCode ?? record.issue_code
  const subjectType = record.subjectType ?? record.subject_type
  const subjectCode = record.subjectCode ?? record.subject_code
  const factType = record.factType ?? record.fact_type
  const ownerRole = record.ownerRole ?? record.owner_role
  if (!issueId || !issueCode || !subjectType || !subjectCode || !factType) return null
  return {
    issueId: String(issueId),
    issueCode: issueCode as ShipmentDataReadinessIssue['issueCode'],
    severity: 'BLOCKING',
    subjectType: subjectType as ShipmentDataReadinessIssue['subjectType'],
    subjectCode: String(subjectCode),
    factType: factType as ShipmentDataReadinessIssue['factType'],
    requiredValue: (record.requiredValue ?? record.required_value ?? null) as
      | string
      | number
      | boolean
      | null,
    actualValue: (record.actualValue ?? record.actual_value ?? null) as
      | string
      | number
      | boolean
      | null,
    unit: typeof record.unit === 'string' ? record.unit : null,
    message: typeof record.message === 'string' ? record.message : '需要补充业务事实。',
    resolution: 'SUPPLY_DATA',
    ownerRole:
      ownerRole === 'WAREHOUSE_OPERATOR' ? 'WAREHOUSE_OPERATOR' : 'WAREHOUSE_OPERATOR',
  }
}

const readinessIssues = computed<ShipmentDataReadinessIssue[]>(() => {
  const detailIssues = detailValue('issues')
  const source = Array.isArray(detailIssues)
    ? detailIssues
    : props.run.dataReadiness?.issues ?? []
  return source
    .map(normalizeIssue)
    .filter((issue): issue is ShipmentDataReadinessIssue => issue !== null)
})

const requestedFacts = computed<ShipmentAgentRequestedFact[]>(() => {
  const detailFacts = detailValue('requestedFacts', 'requested_facts')
  const source = Array.isArray(detailFacts)
    ? detailFacts
    : props.run.dataReadiness?.requestedFacts ?? []
  const normalized = source
    .map(normalizeRequestedFact)
    .filter((fact): fact is ShipmentAgentRequestedFact => fact !== null)
  if (normalized.length > 0) return normalized

  const unique = new Map<string, ShipmentAgentRequestedFact>()
  for (const issue of readinessIssues.value) {
    const key = `${issue.subjectType}:${issue.subjectCode}:${issue.factType}`
    unique.set(key, {
      subjectType: issue.subjectType,
      subjectCode: issue.subjectCode,
      factType: issue.factType,
      unit: issue.unit,
      required: true,
    })
  }
  return [...unique.values()]
})

function factKey(fact: ShipmentAgentRequestedFact): string {
  return `${fact.subjectType}:${fact.subjectCode}:${fact.factType}`
}

function factLabel(fact: ShipmentAgentRequestedFact): string {
  if (fact.label) return fact.label
  const subject = fact.subjectType === 'CARGO_ITEM' ? '货物' : '托盘'
  if (fact.factType === 'AVAILABLE_QUANTITY') {
    return `${subject} ${fact.subjectCode} 的可用数量`
  }
  return `${subject} ${fact.subjectCode} 的主数据是否已核对`
}

function factReason(fact: ShipmentAgentRequestedFact): string {
  if (fact.reason) return fact.reason
  const issue = readinessIssues.value.find(
    (candidate) =>
      candidate.subjectType === fact.subjectType &&
      candidate.subjectCode === fact.subjectCode &&
      candidate.factType === fact.factType,
  )
  return issue?.message ?? '该事实会进入可审计的资料快照，系统不会自行猜测。'
}

function factValueType(fact: ShipmentAgentRequestedFact): 'NUMBER' | 'BOOLEAN' | 'TEXT' {
  if (fact.valueType === 'NUMBER' || fact.valueType === 'INTEGER') return 'NUMBER'
  if (fact.valueType === 'BOOLEAN') return 'BOOLEAN'
  if (fact.factType === 'AVAILABLE_QUANTITY') return 'NUMBER'
  if (fact.factType === 'MASTER_DATA_CONFIRMED') return 'BOOLEAN'
  return 'TEXT'
}

function toLocalDateTimeInput(date: Date): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return [
    date.getFullYear(),
    '-',
    pad(date.getMonth() + 1),
    '-',
    pad(date.getDate()),
    'T',
    pad(date.getHours()),
    ':',
    pad(date.getMinutes()),
  ].join('')
}

const primaryAction = computed<ShipmentAgentAction | null>(() => {
  const allowed = interrupt.value?.allowedActions ?? []
  return (
    (['SUPPLY_DATA', 'CONFIRM', 'SELECT', 'APPROVE', 'RECHECK'] as ShipmentAgentAction[]).find(
      (action) => allowed.includes(action),
    ) ?? null
  )
})

const primaryActionLabel = computed(() => {
  if (primaryAction.value === 'SUPPLY_DATA') return '保存本批数据并继续检查'
  if (primaryAction.value === 'RECHECK') return '重新检查资源绑定'
  const labels: Record<string, string> = {
    SCENARIO_POLICY_CONFIRMATION: '确认本票能力范围',
    DATA_READINESS_REMEDIATION: '保存数据并继续执行',
    RESOURCE_SELECTION: '确认选中资源',
    CASE_ACTIVATION_APPROVAL: '批准启动案件',
    BOOKING_SUBMIT_APPROVAL: '批准提交 LINK',
    BOOKING_RETRY_APPROVAL: '批准人工重试',
    COMPLETION_APPROVAL: '确认结案',
  }
  return labels[interrupt.value?.type ?? ''] ?? '继续运行'
})

const requiresComment = computed(
  () =>
    interrupt.value?.type === 'CASE_ACTIVATION_APPROVAL' ||
    interrupt.value?.type === 'BOOKING_SUBMIT_APPROVAL' ||
    interrupt.value?.type === 'BOOKING_RETRY_APPROVAL' ||
    interrupt.value?.type === 'COMPLETION_APPROVAL',
)

watch(
  interruptKey,
  () => {
    comment.value = ''
    selectedResourceNumber.value = resourceCandidates.value[0] ?? ''
    requiredCapabilities.value =
      props.run.requiredCapabilities.length > 0
        ? [...props.run.requiredCapabilities]
        : ['LOAD_PLANNING', 'LINK_BOOKING']
    factDrafts.value = Object.fromEntries(
      requestedFacts.value.map((fact) => [factKey(fact), '']),
    )
    dataSourceType.value = 'MANUAL_CONFIRMATION'
    sourceReference.value = ''
    sourceLocator.value = ''
    observedAtLocal.value = toLocalDateTimeInput(new Date())
    expiresAtLocal.value = ''
    localError.value = ''
  },
  { immediate: true },
)

function toggleCapability(capability: ShipmentAgentCapability): void {
  if (requiredCapabilities.value.includes(capability)) {
    requiredCapabilities.value = requiredCapabilities.value.filter(
      (candidate) => candidate !== capability,
    )
    return
  }
  requiredCapabilities.value = [...requiredCapabilities.value, capability]
}

function buildDataFacts(): ShipmentAgentDataFact[] | null {
  const observedAt = new Date(observedAtLocal.value)
  if (!observedAtLocal.value || Number.isNaN(observedAt.getTime())) {
    localError.value = '请填写有效的实际观察时间。'
    return null
  }
  if (observedAt.getTime() > Date.now() + 5 * 60 * 1000) {
    localError.value = '实际观察时间不能明显晚于当前时间。'
    return null
  }

  let expiresAt: Date | null = null
  if (expiresAtLocal.value) {
    expiresAt = new Date(expiresAtLocal.value)
    if (Number.isNaN(expiresAt.getTime())) {
      localError.value = '请填写有效的证据有效期。'
      return null
    }
    if (expiresAt.getTime() <= observedAt.getTime()) {
      localError.value = '证据有效期必须晚于实际观察时间。'
      return null
    }
  }

  const result: ShipmentAgentDataFact[] = []
  for (const fact of requestedFacts.value) {
    const draft = factDrafts.value[factKey(fact)]?.trim() ?? ''
    // 支持业务人员分批补数：空项不提交，下一轮评估仍会精准列出尚未完成的事实。
    if (!draft) continue

    let value: string | number | boolean = draft
    const valueType = factValueType(fact)
    if (valueType === 'NUMBER') {
      const numeric = Number(draft)
      if (!Number.isInteger(numeric) || numeric < 0) {
        localError.value = `“${factLabel(fact)}”必须是大于等于 0 的整数。`
        return null
      }
      value = numeric
    } else if (valueType === 'BOOLEAN') {
      if (!['true', 'false'].includes(draft)) {
        localError.value = `请确认“${factLabel(fact)}”。`
        return null
      }
      value = draft === 'true'
    }

    result.push({
      subjectType: fact.subjectType,
      subjectCode: fact.subjectCode,
      factType: fact.factType,
      value,
      sourceType: dataSourceType.value,
      sourceReference: sourceReference.value.trim() || null,
      sourceLocator: sourceLocator.value.trim() || null,
      observedAt: observedAt.toISOString(),
      expiresAt: expiresAt?.toISOString() ?? null,
    })
  }

  if (result.length === 0) {
    localError.value = '请至少填写一项本批次要补充的数据。'
    return null
  }
  if (dataSourceType.value === 'CSV_IMPORT' && !sourceReference.value.trim()) {
    localError.value = '选择 CSV / Excel 库存快照时，请填写文件名或导入批次号。'
    return null
  }
  return result
}

function submit(action: ShipmentAgentAction): void {
  const currentInterrupt = interrupt.value
  if (!currentInterrupt) return

  if (action === 'CONFIRM' && requiredCapabilities.value.length === 0) {
    localError.value = '至少选择一项本票需要的专业能力。'
    return
  }
  if (action === 'SELECT' && !selectedResourceNumber.value) {
    localError.value = `请选择一条${resourceTypeLabel.value}。`
    return
  }
  const dataFacts = action === 'SUPPLY_DATA' ? buildDataFacts() : null
  if (action === 'SUPPLY_DATA' && (!dataFacts || dataFacts.length === 0)) {
    if (!localError.value) localError.value = '当前没有可提交的数据项，请刷新后重试。'
    return
  }
  if (requiresComment.value && action === 'APPROVE' && !comment.value.trim()) {
    localError.value = '请填写本次人工决定的业务说明，便于后续审计。'
    return
  }

  localError.value = ''
  emit('submit', {
    expectedRunVersion: props.run.runVersion,
    interruptType: currentInterrupt.type,
    action,
    comment: comment.value.trim() || null,
    selectedResourceNumber: action === 'SELECT'
      ? selectedResourceNumber.value
      : null,
    requiredCapabilities: action === 'CONFIRM'
      ? [...requiredCapabilities.value]
      : null,
    dataFacts,
  })
}
</script>

<template>
  <section v-if="interrupt" class="shipment-interrupt" :data-type="interrupt.type">
    <div class="interrupt-heading">
      <span class="interrupt-symbol"><UiIcon name="alert" /></span>
      <div>
        <span>当前需要您处理</span>
        <h4>{{ interrupt.title }}</h4>
        <p>{{ interrupt.description }}</p>
      </div>
    </div>

    <section v-if="isDataReadiness" class="readiness-remediation">
      <div class="readiness-remediation-heading">
        <div>
          <span>业务数据待补充</span>
          <strong>
            {{ readinessIssues.length }} 项阻塞正在等待仓库或业务人员处理
          </strong>
          <p>
            可以只填写本次已经核实的项目并分批提交。保存后系统会重新检查；
            仍有缺失或冲突时会继续停在本步骤，不会带着未知数据进入装载与订舱。
          </p>
        </div>
        <span class="readiness-owner">责任角色：仓库业务员</span>
      </div>

      <div v-if="readinessIssues.length" class="readiness-issue-list">
        <article v-for="issue in readinessIssues" :key="issue.issueId">
          <span><UiIcon name="alert" /></span>
          <div>
            <strong>{{ issue.subjectCode }}</strong>
            <p>{{ issue.message }}</p>
            <small>
              {{
                issue.factType === 'AVAILABLE_QUANTITY'
                  ? `需要确认可用数量${issue.unit ? `（${issue.unit}）` : ''}`
                  : '需要确认货物或托盘主数据'
              }}
            </small>
          </div>
        </article>
      </div>

      <div v-if="requestedFacts.length" class="readiness-fact-grid">
        <label v-for="fact in requestedFacts" :key="factKey(fact)" class="readiness-fact-field">
          <span>
            <strong>{{ factLabel(fact) }}</strong>
            <em v-if="fact.unit">{{ fact.unit }}</em>
          </span>
          <select
            v-if="factValueType(fact) === 'BOOLEAN'"
            v-model="factDrafts[factKey(fact)]"
            :disabled="busy"
          >
            <option value="">请选择核对结果</option>
            <option value="true">已核对，数据真实可用</option>
            <option value="false">尚未核对或不能确认</option>
          </select>
          <input
            v-else
            v-model="factDrafts[factKey(fact)]"
            :type="factValueType(fact) === 'NUMBER' ? 'number' : 'text'"
            :min="factValueType(fact) === 'NUMBER' ? 0 : undefined"
            :step="factValueType(fact) === 'NUMBER' ? 1 : undefined"
            :disabled="busy"
            :placeholder="
              factValueType(fact) === 'NUMBER'
                ? '填写当前实际可用数量'
                : '填写已核实的业务事实'
            "
          />
          <small>{{ factReason(fact) }}</small>
        </label>
      </div>

      <div v-else class="interrupt-guidance">
        <UiIcon name="refresh" />
        <p>数据请求明细尚未同步完整，请刷新当前运行后再提交，避免补到错误对象。</p>
      </div>

      <div class="readiness-source-grid">
        <label class="readiness-fact-field">
          <span><strong>数据来源</strong></span>
          <select v-model="dataSourceType" :disabled="busy">
            <option value="MANUAL_CONFIRMATION">业务人员现场确认</option>
            <option value="CSV_IMPORT">CSV / Excel 库存快照</option>
            <option value="SYNTHETIC_DEMO">脱敏演示数据（仅用于学习验收）</option>
          </select>
          <small>
            来源会进入审计记录。当前尚未接入 ERP / WMS，页面不会允许把人工输入伪装成企业系统数据。
          </small>
        </label>
        <label class="readiness-fact-field">
          <span>
            <strong>来源说明</strong>
            <em>{{ dataSourceType === 'CSV_IMPORT' ? '必填' : '可选' }}</em>
          </span>
          <input
            v-model="sourceReference"
            type="text"
            :disabled="busy"
            placeholder="例如：仓库盘点表 2026-10-16"
          />
          <small>填写文件名、单据号或人工确认记录。</small>
        </label>
        <label class="readiness-fact-field">
          <span><strong>证据位置</strong><em>可选</em></span>
          <input
            v-model="sourceLocator"
            type="text"
            :disabled="busy"
            placeholder="例如：Sheet1!F12 或仓位 A-03"
          />
          <small>便于后续复核具体页码、单元格或仓位。</small>
        </label>
        <label class="readiness-fact-field">
          <span><strong>实际观察时间</strong></span>
          <input
            v-model="observedAtLocal"
            type="datetime-local"
            :disabled="busy"
          />
          <small>填写盘点、核对或导出快照实际发生的本地时间。</small>
        </label>
        <label class="readiness-fact-field">
          <span><strong>证据有效期</strong><em>可选</em></span>
          <input
            v-model="expiresAtLocal"
            type="datetime-local"
            :disabled="busy"
          />
          <small>如填写，必须晚于观察时间；过期后系统会重新要求核实。</small>
        </label>
      </div>

      <div class="readiness-batch-note">
        <UiIcon name="layers" />
        <p>
          本批次填写项共用同一来源、观察时间和有效期。若库存表、现场核对等来源不同，
          请分成多个批次提交，便于审计和后续追溯。
        </p>
      </div>
    </section>

    <div
      v-if="isScenarioPolicy && interrupt.allowedActions.includes('CONFIRM')"
      class="capability-selector"
    >
      <button
        v-for="option in capabilityOptions"
        :key="option.value"
        type="button"
        :class="{ selected: requiredCapabilities.includes(option.value) }"
        :disabled="busy"
        @click="toggleCapability(option.value)"
      >
        <span class="capability-check">
          <UiIcon v-if="requiredCapabilities.includes(option.value)" name="check" />
        </span>
        <span>
          <strong>{{ option.label }}</strong>
          <small>{{ option.description }}</small>
        </span>
      </button>
    </div>

    <label
      v-if="isResourceSelection && interrupt.allowedActions.includes('SELECT')"
      class="interrupt-field"
    >
      <span>选择本次运行使用的{{ resourceTypeLabel }}</span>
      <select v-model="selectedResourceNumber" :disabled="busy">
        <option value="">请选择</option>
        <option v-for="candidate in resourceCandidates" :key="candidate" :value="candidate">
          {{ candidate }}
        </option>
      </select>
      <small>系统只保存稳定编号，不复制专业系统中的业务明细。</small>
    </label>

    <div
      v-else-if="isResourceSelection && interrupt.allowedActions.includes('RECHECK')"
      class="interrupt-guidance"
    >
      <UiIcon name="layers" />
      <p>
        当前还没有可选择的{{ resourceTypeLabel }}。请先在专业工作台创建并绑定资源，
        然后回到这里重新检查；系统不会替你生成虚假的业务数据。
      </p>
    </div>

    <label v-if="requiresComment" class="interrupt-field">
      <span>人工决定说明</span>
      <textarea
        v-model="comment"
        rows="3"
        :disabled="busy"
        placeholder="说明核对内容、批准依据或结案原因"
      ></textarea>
    </label>

    <div v-if="localError || error" class="interrupt-error" role="alert">
      {{ localError || error }}
    </div>

    <div class="interrupt-actions">
      <button
        v-if="interrupt.allowedActions.includes('STOP')"
        class="interrupt-stop"
        type="button"
        :disabled="busy"
        @click="submit('STOP')"
      >
        停止本次运行
      </button>
      <button
        v-if="primaryAction"
        class="interrupt-primary"
        type="button"
        :disabled="busy"
        @click="submit(primaryAction)"
      >
        <UiIcon name="check" />
        {{ busy ? '正在执行…' : primaryActionLabel }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.shipment-interrupt {
  padding: 18px;
  border: 1px solid #ead7af;
  border-radius: 15px;
  background:
    linear-gradient(135deg, rgba(255, 249, 235, 0.96), rgba(255, 255, 255, 0.98));
}

.interrupt-heading {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 13px;
  align-items: start;
}

.interrupt-symbol {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 11px;
  color: #8f671d;
  background: #f8e7bf;
}

.interrupt-symbol :deep(.ui-icon) {
  width: 18px;
}

.interrupt-heading > div > span {
  color: #a17d38;
  font-size: 11px;
  font-weight: 780;
  letter-spacing: 0.14em;
}

.interrupt-heading h4,
.interrupt-heading p {
  margin: 0;
}

.interrupt-heading h4 {
  margin-top: 5px;
  color: #342e23;
  font-size: 18px;
}

.interrupt-heading p {
  margin-top: 5px;
  color: #756b58;
  font-size: 13px;
  line-height: 1.65;
}

.capability-selector {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
  margin-top: 16px;
}

.capability-selector button {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 12px;
  border: 1px solid #e6dfd0;
  border-radius: 11px;
  color: #5f5b53;
  background: rgba(255, 255, 255, 0.8);
  cursor: pointer;
  text-align: left;
}

.capability-selector button.selected {
  border-color: #9fc7b6;
  color: #254f42;
  background: #f1f8f5;
}

.capability-check {
  display: grid;
  width: 20px;
  height: 20px;
  place-items: center;
  border: 1px solid #d9d2c4;
  border-radius: 6px;
  background: #fff;
}

.selected .capability-check {
  border-color: #4f8874;
  color: #fff;
  background: #4f8874;
}

.capability-check :deep(.ui-icon) {
  width: 13px;
}

.capability-selector strong,
.capability-selector small {
  display: block;
}

.capability-selector strong {
  font-size: 13px;
}

.capability-selector small {
  margin-top: 4px;
  color: #8c887f;
  font-size: 12px;
  line-height: 1.45;
}

.interrupt-field {
  display: grid;
  gap: 7px;
  margin-top: 15px;
}

.interrupt-field > span {
  color: #625b4e;
  font-size: 12px;
  font-weight: 700;
}

.interrupt-field select,
.interrupt-field textarea {
  width: 100%;
  border: 1px solid #ded7c9;
  border-radius: 9px;
  outline: none;
  color: #34312b;
  background: #fff;
  font-size: 13px;
}

.interrupt-field select {
  height: 38px;
  padding: 0 10px;
}

.interrupt-field textarea {
  padding: 10px;
  resize: vertical;
  line-height: 1.55;
}

.interrupt-field select:focus,
.interrupt-field textarea:focus {
  border-color: #8fb6a5;
  box-shadow: 0 0 0 3px rgba(79, 136, 116, 0.1);
}

.interrupt-field small {
  color: #9a9488;
  font-size: 12px;
}

.interrupt-guidance {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  margin-top: 15px;
  padding: 11px 12px;
  border: 1px solid #e6dfd0;
  border-radius: 10px;
  color: #706758;
  background: rgba(255, 255, 255, 0.72);
}

.interrupt-guidance :deep(.ui-icon) {
  width: 16px;
  flex: 0 0 16px;
  color: #8b7443;
}

.interrupt-guidance p {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}

.interrupt-error {
  margin-top: 12px;
  padding: 9px 11px;
  border: 1px solid #edcccc;
  border-radius: 8px;
  color: #914848;
  background: #fff7f7;
  font-size: 12px;
}

.interrupt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 15px;
}

.interrupt-actions button {
  min-height: 36px;
  padding: 0 13px;
  border-radius: 9px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.interrupt-actions button:disabled {
  cursor: wait;
  opacity: 0.58;
}

.interrupt-stop {
  border: 1px solid #dfd8ca;
  color: #776f61;
  background: #fff;
}

.interrupt-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #376d5c;
  color: #fff;
  background: #376d5c;
}

.interrupt-primary :deep(.ui-icon) {
  width: 14px;
}

.readiness-remediation {
  display: grid;
  gap: 14px;
  margin-top: 16px;
  padding: 16px;
  border: 1px solid #e2d2ae;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.78);
}

.readiness-remediation-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.readiness-remediation-heading > div > span,
.readiness-remediation-heading strong,
.readiness-remediation-heading p {
  display: block;
}

.readiness-remediation-heading > div > span {
  color: #9b762f;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.08em;
}

.readiness-remediation-heading strong {
  margin-top: 5px;
  color: #3e3628;
  font-size: 15px;
}

.readiness-remediation-heading p {
  max-width: 700px;
  margin: 6px 0 0;
  color: #756b58;
  font-size: 12px;
  line-height: 1.65;
}

.readiness-owner {
  flex: 0 0 auto;
  padding: 6px 9px;
  border-radius: 999px;
  color: #775f31;
  background: #f5ead2;
  font-size: 11px;
  font-weight: 650;
}

.readiness-issue-list {
  display: grid;
  gap: 8px;
}

.readiness-issue-list article {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid #ebddc1;
  border-radius: 10px;
  background: #fffaf1;
}

.readiness-issue-list article > span {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 8px;
  color: #966a21;
  background: #f6e8c9;
}

.readiness-issue-list article > span :deep(.ui-icon) {
  width: 15px;
}

.readiness-issue-list strong,
.readiness-issue-list p,
.readiness-issue-list small {
  display: block;
  margin: 0;
}

.readiness-issue-list strong {
  color: #493f30;
  font-size: 13px;
}

.readiness-issue-list p {
  margin-top: 3px;
  color: #6f6553;
  font-size: 12px;
  line-height: 1.55;
}

.readiness-issue-list small {
  margin-top: 4px;
  color: #9a8c73;
  font-size: 11px;
}

.readiness-fact-grid,
.readiness-source-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 11px;
}

.readiness-source-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  padding-top: 13px;
  border-top: 1px solid #eee4d2;
}

.readiness-batch-note {
  display: flex;
  gap: 9px;
  align-items: flex-start;
  padding: 10px 11px;
  border: 1px solid #dce6e1;
  border-radius: 9px;
  color: #5f746b;
  background: #f5f9f7;
}

.readiness-batch-note :deep(.ui-icon) {
  width: 15px;
  flex: 0 0 15px;
  margin-top: 1px;
}

.readiness-batch-note p {
  margin: 0;
  font-size: 11px;
  line-height: 1.55;
}

.readiness-fact-field {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.readiness-fact-field > span {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.readiness-fact-field strong {
  color: #514838;
  font-size: 12px;
}

.readiness-fact-field em {
  color: #9a8b72;
  font-size: 11px;
  font-style: normal;
}

.readiness-fact-field input,
.readiness-fact-field select {
  width: 100%;
  height: 40px;
  min-width: 0;
  padding: 0 11px;
  border: 1px solid #ddd4c4;
  border-radius: 9px;
  outline: none;
  color: #34312b;
  background: #fff;
  font-size: 13px;
}

.readiness-fact-field input:focus,
.readiness-fact-field select:focus {
  border-color: #79a38f;
  box-shadow: 0 0 0 3px rgba(79, 136, 116, 0.1);
}

.readiness-fact-field small {
  color: #8d8372;
  font-size: 11px;
  line-height: 1.5;
}

@media (max-width: 680px) {
  .interrupt-heading {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .interrupt-version {
    grid-column: 2;
    justify-self: start;
  }

  .capability-selector {
    grid-template-columns: 1fr;
  }

  .readiness-remediation-heading,
  .readiness-fact-grid,
  .readiness-source-grid {
    grid-template-columns: 1fr;
  }

  .readiness-remediation-heading {
    display: grid;
  }

  .readiness-owner {
    justify-self: start;
  }
}
</style>
