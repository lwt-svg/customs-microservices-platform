<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

import {
  createShipmentCase,
  PlatformApiError,
  type CreateShipmentCaseInput,
  type ShipmentCase,
  type ShipmentTransportScenario,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import {
  clearPendingShipmentCaseCommand,
  getOrCreateShipmentCaseCommandKey,
  getPendingShipmentCaseCommand,
} from '@/components/shipment-case/shipmentCasePendingCommands'

const props = defineProps<{ modelValue: boolean }>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [shipmentCase: ShipmentCase]
}>()

const submitting = ref(false)
const formError = ref('')
const recoveredCommandNotice = ref('')

const form = reactive({
  caseNumber: '',
  caseName: '',
  transportScenario: 'UNDECIDED' as ShipmentTransportScenario,
  description: '',
})

const CREATE_COMMAND_SCOPE = 'create-form'

const scenarioOptions: Array<{
  value: ShipmentTransportScenario
  label: string
  hint: string
}> = [
  { value: 'UNDECIDED', label: '待确认', hint: '运输方式尚未由业务确认' },
  { value: 'SEA_FCL', label: '海运整柜', hint: '整柜运输，可并行推进装载与订舱' },
  { value: 'SEA_LCL', label: '海运拼箱', hint: '由货代或集拼仓完成最终拼柜' },
  { value: 'AIR', label: '空运', hint: '跳过海运集装箱与 VGM 流程' },
  { value: 'EXPRESS', label: '国际快递', hint: '适用于快递或小包业务' },
  { value: 'LOAD_PLANNING_ONLY', label: '仅装载规划', hint: '不强制创建订舱申请' },
  { value: 'BOOKING_ONLY', label: '仅订舱', hint: '已有上游数据时直接推进订舱' },
]

watch(
  () => props.modelValue,
  (open) => {
    if (open) restoreOrResetForm()
  },
)

function resetFormFields(): void {
  form.caseNumber = ''
  form.caseName = ''
  form.transportScenario = 'UNDECIDED'
  form.description = ''
  formError.value = ''
  recoveredCommandNotice.value = ''
}

/**
 * 抽屉重新打开或页面刷新后，恢复“结果尚未确认”的创建命令。
 *
 * fingerprint 本身就是规范化请求 JSON，因此这里可以把表单也恢复出来。用户无需凭记忆
 * 重新输入一遍；直接再次提交时，存储 helper 会返回同一个 Idempotency-Key。
 */
function restoreOrResetForm(): void {
  const pending = getPendingShipmentCaseCommand('CREATE_CASE', CREATE_COMMAND_SCOPE)
  if (!pending) {
    resetFormFields()
    return
  }

  try {
    const input = JSON.parse(pending.fingerprint) as Partial<CreateShipmentCaseInput>
    const knownScenario = scenarioOptions.some(
      (option) => option.value === input.transportScenario,
    )
    if (
      typeof input.caseNumber !== 'string' ||
      typeof input.caseName !== 'string' ||
      !knownScenario ||
      (input.description !== null && typeof input.description !== 'string')
    ) {
      throw new Error('Invalid pending create command')
    }

    form.caseNumber = input.caseNumber
    form.caseName = input.caseName
    form.transportScenario = input.transportScenario as ShipmentTransportScenario
    form.description = input.description ?? ''
    formError.value = ''
    recoveredCommandNotice.value =
      '已恢复上次结果尚未确认的创建命令。保持内容不变再次提交，会复用原幂等键。'
  } catch {
    clearPendingShipmentCaseCommand('CREATE_CASE', CREATE_COMMAND_SCOPE)
    resetFormFields()
  }
}

/**
 * 用户主动改动字段后，语义已经不再是“重试原命令”。
 * 立即丢弃旧 key；即使之后又改回原文字，也会被视为一条经过重新确认的新命令。
 */
function discardPendingCreateCommand(): void {
  clearPendingShipmentCaseCommand('CREATE_CASE', CREATE_COMMAND_SCOPE)
  recoveredCommandNotice.value = ''
}

function reset(): void {
  discardPendingCreateCommand()
  resetFormFields()
}

function close(): void {
  if (!submitting.value) {
    emit('update:modelValue', false)
  }
}

function validate(): string {
  const caseNumber = form.caseNumber.trim()
  if (!caseNumber) return '请输入案件编号。'
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(caseNumber)) {
    return '案件编号只能包含字母、数字、下划线和短横线。'
  }
  if (caseNumber.length > 64) return '案件编号不能超过 64 个字符。'
  if (!form.caseName.trim()) return '请输入案件名称。'
  if (form.caseName.trim().length > 128) return '案件名称不能超过 128 个字符。'
  if (form.description.trim().length > 2000) return '业务说明不能超过 2000 个字符。'
  return ''
}

function toInput(): CreateShipmentCaseInput {
  return {
    caseNumber: form.caseNumber.trim(),
    caseName: form.caseName.trim(),
    transportScenario: form.transportScenario,
    description: form.description.trim() || null,
  }
}

function commandKeyFor(input: CreateShipmentCaseInput): string {
  const fingerprint = JSON.stringify(input)
  return getOrCreateShipmentCaseCommandKey({
    commandType: 'CREATE_CASE',
    scope: CREATE_COMMAND_SCOPE,
    fingerprint,
    keyPrefix: 'shipment-case-create',
  })
}

async function submit(): Promise<void> {
  formError.value = validate()
  if (formError.value) return

  const input = toInput()
  const fingerprint = JSON.stringify(input)
  const idempotencyKey = commandKeyFor(input)
  submitting.value = true

  try {
    const created = await createShipmentCase(input, idempotencyKey)
    clearPendingShipmentCaseCommand(
      'CREATE_CASE',
      CREATE_COMMAND_SCOPE,
      fingerprint,
    )
    emit('created', created)
    emit('update:modelValue', false)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建发运案件失败'

    // 400/409 是已经得到的确定响应。用户修正后应形成新命令；网络或 5xx 结果不明确时
    // 则保留原 key，让“再次提交”成为安全的同命令重试。
    if (error instanceof PlatformApiError && [400, 409].includes(error.status)) {
      clearPendingShipmentCaseCommand(
        'CREATE_CASE',
        CREATE_COMMAND_SCOPE,
        fingerprint,
      )
      recoveredCommandNotice.value = ''
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div v-if="modelValue" class="drawer-backdrop" @click.self="close">
    <aside class="form-drawer shipment-create-drawer" aria-labelledby="shipment-create-title">
      <div class="drawer-header">
        <div>
          <span class="shipment-drawer-kicker">NEW SHIPMENT CASE</span>
          <h3 id="shipment-create-title">创建发运案件</h3>
          <p>建立一票发运工作的统一编号，后续可关联装载任务与 LINK 订舱申请。</p>
        </div>
        <button
          class="close-button"
          type="button"
          aria-label="关闭"
          :disabled="submitting"
          @click="close"
        >
          <UiIcon name="close" />
        </button>
      </div>

      <form class="shipment-create-form" @submit.prevent="submit">
        <div v-if="recoveredCommandNotice" class="feedback-banner warning" role="status">
          <span>!</span>
          <p>{{ recoveredCommandNotice }}</p>
        </div>

        <section class="shipment-form-section">
          <div class="shipment-form-heading">
            <span>01</span>
            <div>
              <strong>案件标识</strong>
              <small>案件编号创建后将作为跨系统稳定查询标识</small>
            </div>
          </div>

          <label class="form-field">
            <span>案件编号 <em>必填</em></span>
            <input
              v-model="form.caseNumber"
              maxlength="64"
              autocomplete="off"
              placeholder="例如 SHP-20261015-001"
              @input="discardPendingCreateCommand"
            />
            <small>建议使用 SHP- 前缀；允许字母、数字、下划线和短横线。</small>
          </label>

          <label class="form-field">
            <span>案件名称 <em>必填</em></span>
            <input
              v-model="form.caseName"
              maxlength="128"
              autocomplete="off"
              placeholder="例如 上海至洛杉矶十月发运"
              @input="discardPendingCreateCommand"
            />
          </label>
        </section>

        <section class="shipment-form-section">
          <div class="shipment-form-heading">
            <span>02</span>
            <div>
              <strong>运输场景</strong>
              <small>场景用于后续编排，不会自动创建或提交下游业务</small>
            </div>
          </div>

          <label class="form-field">
            <span>运输场景 <em>必填</em></span>
            <select v-model="form.transportScenario" @change="discardPendingCreateCommand">
              <option
                v-for="option in scenarioOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }} · {{ option.hint }}
              </option>
            </select>
          </label>

          <label class="form-field">
            <span>业务说明</span>
            <textarea
              v-model="form.description"
              maxlength="2000"
              rows="5"
              placeholder="填写本票发运的范围、交付要求或需要协同的事项"
              @input="discardPendingCreateCommand"
            ></textarea>
          </label>
        </section>

        <div v-if="formError" class="inline-error" role="alert">{{ formError }}</div>

        <div class="drawer-actions shipment-drawer-actions">
          <button class="secondary-button" type="button" :disabled="submitting" @click="reset">
            重置
          </button>
          <button class="secondary-button" type="button" :disabled="submitting" @click="close">
            关闭
          </button>
          <button class="primary-button" type="submit" :disabled="submitting">
            {{ submitting ? '正在创建…' : '创建案件' }}
          </button>
        </div>
      </form>
    </aside>
  </div>
</template>
