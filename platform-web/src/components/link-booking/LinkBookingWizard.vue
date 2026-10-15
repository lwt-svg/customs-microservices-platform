<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import {
  changeLinkBookingApplicationStatus,
  type LinkBookingApplication,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{
  modelValue: boolean
  application: LinkBookingApplication | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submitted: [application: LinkBookingApplication]
}>()

type WizardStep = 1 | 2 | 3 | 4 | 5 | 6 | 7

const currentStep = ref<WizardStep>(1)
const validationError = ref('')
const submitting = ref(false)
const saved = ref(false)
const saveMessage = ref('')
const autoFilling = ref(false)
const autoMessage = ref('')
const completedSteps = ref<WizardStep[]>([])
let automationTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 这是本地模拟 LINK 自动填单，而非真实 LINK 网站自动化。
 *
 * 页面打开后，机器人依次把已经识别并人工确认的数据写入七步表单，自动停在 Recap 并完成
 * DEMO SAVE。业务人员只负责最终复核和 VALIDATE；VALIDATE 才会调用后端，把 DRAFT 变成
 * SUBMITTED，后续仍由异步幂等投递链路处理。
 */
const form = reactive({
  linkAccount: 'DEMO-LINK-OPERATOR',
  originPort: '',
  destinationPort: '',
  transportMode: 'SEA',
  pickupDate: '',
  deliveryDate: '',
  incoterm: 'FCA',
  commodityName: 'DEMO-GENERAL-CARGO',
  packageCount: '1',
  palletCount: '1',
  volumeCbm: '12.50',
  grossWeightKg: '1200',
  packageType: 'PALLET',
  purchaseOrderNumber: '',
  bookingQuantity: '1',
  shipperName: 'DEMO-SHIPPER-CO',
  consigneeName: 'DEMO-CONSIGNEE-CO',
  notifyPartyName: 'DEMO-NOTIFY-PARTY',
  attachmentName: '',
})

const steps: Array<{ number: WizardStep; title: string; description: string }> = [
  { number: 1, title: '登录 LINK', description: '演示账号与边界说明' },
  { number: 2, title: 'Destination', description: '航线、日期与运输方式' },
  { number: 3, title: 'Package', description: '包裹、托数、体积与重量' },
  { number: 4, title: 'Purchase Order', description: '挂载 PO 和数量' },
  { number: 5, title: 'Participants', description: '发货人与收货人' },
  { number: 6, title: 'Attachment', description: '附件说明' },
  { number: 7, title: 'Recap', description: '汇总、SAVE 与 VALIDATE' },
]

const currentStepMeta = computed<{ number: WizardStep; title: string; description: string }>(() => {
  const matchingStep = steps.find((step) => step.number === currentStep.value)
  return matchingStep ?? steps[0]!
})

const packageRows = computed(() => props.application?.bookingDetails?.packages ?? [])
const poRows = computed(() =>
  packageRows.value.flatMap((packageGroup, packageIndex) =>
    packageGroup.poAllocations.map((allocation) => ({
      packageName: `包${packageIndex + 1}`,
      description: packageGroup.description,
      poNumber: allocation.poNumber,
      quantity: allocation.quantity,
    })),
  ),
)

function automationValues(application: LinkBookingApplication): typeof form {
  const details = application.bookingDetails
  const packageDescriptions = [
    ...new Set(
      (details?.packages ?? [])
        .map((packageGroup) => packageGroup.description?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  ]
  const poAllocations = (details?.packages ?? []).flatMap(
    (packageGroup) => packageGroup.poAllocations,
  )
  const poNumbers = [
    ...new Set(
      poAllocations
        .map((allocation) => allocation.poNumber.trim())
      .filter((value) => Boolean(value)),
    ),
  ]

  return {
    linkAccount: 'DEMO-LINK-ROBOT',
    originPort: application.originPort,
    destinationPort: application.destinationPort,
    transportMode: application.transportMode,
    pickupDate: application.requestedEtd ?? '',
    deliveryDate: application.requestedEtd ?? '',
    incoterm: 'FCA',
    commodityName: packageDescriptions.join(' / ') || 'GENERAL CARGO',
    packageCount: String(details?.packages.length || 1),
    palletCount: String(details?.totalPallets ?? 1),
    volumeCbm: String(details?.totalCbm ?? 0),
    grossWeightKg: String(details?.totalGrossWeightKg ?? 0),
    packageType: 'PALLET',
    purchaseOrderNumber: poNumbers.join(' / ') || `PO-${application.applicationNumber}`,
    bookingQuantity: String(
      details?.totalQuantity ||
        poAllocations.reduce((sum, allocation) => sum + (allocation.quantity ?? 0), 0) ||
        1,
    ),
    shipperName: details?.shipper || '待业务补充',
    consigneeName: details?.consignee || '待业务补充',
    notifyPartyName: details?.notifyParty || '待业务补充',
    attachmentName: details?.invoiceNumber ? `PL ${details.invoiceNumber}.pdf` : '',
  }
}

function clearAutomationTimer(): void {
  if (automationTimer) {
    clearTimeout(automationTimer)
    automationTimer = null
  }
}

function resetAutomationForm(): void {
  Object.assign(form, {
    linkAccount: '',
    originPort: '',
    destinationPort: '',
    transportMode: '',
    pickupDate: '',
    deliveryDate: '',
    incoterm: '',
    commodityName: '',
    packageCount: '',
    palletCount: '',
    volumeCbm: '',
    grossWeightKg: '',
    packageType: '',
    purchaseOrderNumber: '',
    bookingQuantity: '',
    shipperName: '',
    consigneeName: '',
    notifyPartyName: '',
    attachmentName: '',
  })
}

function applyAutomationStep(step: WizardStep, values: typeof form): void {
  if (step === 1) {
    form.linkAccount = values.linkAccount
    autoMessage.value = '机器人已登录本地模拟 LINK。'
  } else if (step === 2) {
    Object.assign(form, {
      originPort: values.originPort,
      destinationPort: values.destinationPort,
      transportMode: values.transportMode,
      pickupDate: values.pickupDate,
      deliveryDate: values.deliveryDate,
      incoterm: values.incoterm,
    })
    autoMessage.value = '已填写固定路线、运输方式、日期和贸易条款。'
  } else if (step === 3) {
    Object.assign(form, {
      commodityName: values.commodityName,
      packageCount: values.packageCount,
      palletCount: values.palletCount,
      volumeCbm: values.volumeCbm,
      grossWeightKg: values.grossWeightKg,
      packageType: values.packageType,
    })
    autoMessage.value = `已填写 ${packageRows.value.length || 1} 个 Package 和装箱总计。`
  } else if (step === 4) {
    form.purchaseOrderNumber = values.purchaseOrderNumber
    form.bookingQuantity = values.bookingQuantity
    autoMessage.value = `已挂载 ${poRows.value.length || 1} 条 PO allocation。`
  } else if (step === 5) {
    form.shipperName = values.shipperName
    form.consigneeName = values.consigneeName
    form.notifyPartyName = values.notifyPartyName
    autoMessage.value = '已填写 Shipper、Consignee 和 Notify Party。'
  } else if (step === 6) {
    form.attachmentName = values.attachmentName
    autoMessage.value = '已填写附件说明；本地演示不会上传到真实 LINK。'
  } else {
    const automationError =
      validateStep(1) ||
      validateStep(2) ||
      validateStep(3) ||
      validateStep(4) ||
      validateStep(5) ||
      validateStep(6)
    if (automationError) {
      saved.value = false
      validationError.value = `自动填单未完成：${automationError} 请返回 PDF 复核重新补齐业务字段。`
      autoMessage.value = '机器人已停在 Recap，但发现业务必填字段缺失。'
    } else {
      saved.value = true
      saveMessage.value = '机器人已完成七步填单和 DEMO SAVE，请业务人员核对后点击 VALIDATE。'
      autoMessage.value = '自动填单完成，已停在 Recap 等待业务确认。'
    }
    autoFilling.value = false
  }
  completedSteps.value = [...completedSteps.value, step]
}

function startAutomation(application: LinkBookingApplication): void {
  clearAutomationTimer()
  resetAutomationForm()
  currentStep.value = 1
  validationError.value = ''
  saved.value = false
  saveMessage.value = ''
  autoFilling.value = true
  autoMessage.value = '正在启动本地 LINK 自动填单机器人…'
  completedSteps.value = []
  const values = automationValues(application)

  const runStep = (step: WizardStep): void => {
    currentStep.value = step
    applyAutomationStep(step, values)
    if (step < 7) {
      automationTimer = setTimeout(() => runStep((step + 1) as WizardStep), 450)
    }
  }
  automationTimer = setTimeout(() => runStep(1), 300)
}

watch(
  () => [props.modelValue, props.application] as const,
  ([open, application]) => {
    if (open && application) startAutomation(application)
    if (!open) clearAutomationTimer()
  },
)

function close(): void {
  if (!submitting.value) {
    clearAutomationTimer()
    emit('update:modelValue', false)
  }
}

function openStep(step: WizardStep): void {
  if (submitting.value || autoFilling.value || !completedSteps.value.includes(step)) return
  validationError.value = ''
  currentStep.value = step
}

function validateStep(step: WizardStep): string {
  if (step === 1 && !isDemoText(form.linkAccount)) {
    return '操作员必须使用 DEMO- 前缀的构造名称。'
  }
  if (step === 2) {
    if (!form.originPort.trim() || !form.destinationPort.trim()) return '请填写起运地和目的地。'
    if (!form.transportMode.trim()) return '请选择运输方式。'
    if (!form.pickupDate || !form.deliveryDate) return '请填写 Pickup 和 Delivery 日期。'
    if (!['FCA', 'FOB', 'EXW'].includes(form.incoterm)) return '请选择支持的贸易条款。'
  }
  if (step === 3) {
    if (!form.commodityName.trim()) return '请填写品名。'
    if (!positiveNumber(form.packageCount) || !positiveNumber(form.palletCount)) {
      return '包裹数和托数必须是大于 0 的数字。'
    }
    if (!positiveNumber(form.volumeCbm) || !positiveNumber(form.grossWeightKg)) {
      return '体积和毛重必须是大于 0 的数字。'
    }
  }
  if (step === 4) {
    if (!form.purchaseOrderNumber.trim()) return '请填写 PO 编号。'
    if (!positiveNumber(form.bookingQuantity)) return '订舱数量必须是大于 0 的数字。'
  }
  if (step === 5) {
    if (!form.shipperName.trim() || !form.consigneeName.trim() || !form.notifyPartyName.trim()) {
      return '请填写发货人、收货人和通知人。'
    }
  }
  return ''
}

function isDemoText(value: string): boolean {
  return /^DEMO-[A-Za-z0-9_.-]+$/.test(value.trim())
}

function positiveNumber(value: string): boolean {
  return Number.isFinite(Number(value)) && Number(value) > 0
}

function next(): void {
  validationError.value = validateStep(currentStep.value)
  if (validationError.value || currentStep.value === 7) return
  currentStep.value = (currentStep.value + 1) as WizardStep
}

function previous(): void {
  if (currentStep.value <= 1 || submitting.value) return
  validationError.value = ''
  currentStep.value = (currentStep.value - 1) as WizardStep
}

/**
 * SAVE 只代表当前七步页面的本地演示已检查，不写入后端。
 *
 * VALIDATE 才会改变 Python 的业务状态：DRAFT → SUBMITTED。这样页面不会绕开已有的
 * 异步投递、幂等键与承运人回调练习。
 */
async function validateAndSubmit(): Promise<void> {
  const application = props.application
  if (!application) return

  if (!saved.value) {
    validationError.value = '请先点击 SAVE，确认 Recap 内容后再由业务执行 VALIDATE。'
    return
  }

  validationError.value =
    validateStep(1) ||
    validateStep(2) ||
    validateStep(3) ||
    validateStep(4) ||
    validateStep(5) ||
    validateStep(6)
  if (validationError.value) return

  submitting.value = true
  try {
    const changed = await changeLinkBookingApplicationStatus(
      application.applicationNumber,
      'SUBMITTED',
    )
    emit('submitted', changed)
    emit('update:modelValue', false)
  } catch (error) {
    validationError.value = error instanceof Error ? error.message : '模拟 LINK VALIDATE 失败'
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(clearAutomationTimer)
</script>

<template>
  <div v-if="modelValue && application" class="drawer-backdrop" @click.self="close">
    <aside class="form-drawer link-wizard-drawer" aria-labelledby="link-wizard-title">
      <div class="drawer-header">
        <div>
          <h3 id="link-wizard-title">模拟 LINK 七步自动填单</h3>
          <p>{{ application.applicationNumber }} · 机器人自动填写，业务只做最终确认。</p>
        </div>
        <button class="close-button" type="button" aria-label="关闭" :disabled="submitting" @click="close">
          <UiIcon name="close" />
        </button>
      </div>

      <ol class="link-wizard-steps" aria-label="模拟 LINK 七步">
        <li
          v-for="step in steps"
          :key="step.number"
          :class="{
            active: currentStep === step.number,
            completed: completedSteps.includes(step.number),
          }"
        >
          <button
            type="button"
            :disabled="submitting || autoFilling || !completedSteps.includes(step.number)"
            @click="openStep(step.number)"
          >
            <b>{{ step.number }}</b>
            <span>{{ step.title }}</span>
          </button>
        </li>
      </ol>

      <div class="link-wizard-heading">
        <span>第 {{ currentStepMeta.number }} 步</span>
        <h4>{{ currentStepMeta.title }}</h4>
        <p>{{ currentStepMeta.description }}</p>
      </div>

      <div class="link-automation-progress" :class="{ complete: !autoFilling && saved }" role="status">
        <span>{{ autoFilling ? 'AUTO' : 'DONE' }}</span>
        <div>
          <strong>{{ autoFilling ? '机器人正在自动填写' : '机器人已完成自动填写' }}</strong>
          <p>{{ autoMessage }}</p>
        </div>
      </div>

      <section v-if="currentStep === 1" class="link-wizard-panel">
        <div class="feedback-banner success">
          <span>✓</span>
          <p>机器人自动登录本地模拟页面，不保存账号，也不会打开真实 LINK。</p>
        </div>
        <label class="form-field">
          <span>自动化执行账号</span>
          <input v-model="form.linkAccount" readonly />
          <small>由本地机器人自动填写，不发送给任何外部系统。</small>
        </label>
      </section>

      <section v-else-if="currentStep === 2" class="link-wizard-panel">
        <div class="feedback-banner warning">
          <span>!</span>
          <p>起运地、目的地、运输方式与日期属于业务固定/人工补充字段，不是装箱单识别结果。</p>
        </div>
        <label class="form-field">
          <span>起运地 / Origin（业务补充）<em>必填</em></span>
          <input v-model="form.originPort" readonly />
        </label>
        <label class="form-field">
          <span>目的地 / Destination（业务补充）<em>必填</em></span>
          <input v-model="form.destinationPort" readonly />
        </label>
        <label class="form-field">
          <span>运输方式 <em>必填</em></span>
          <select v-model="form.transportMode" disabled>
            <option value="SEA">SEA（海运）</option>
            <option value="AIR">AIR（空运）</option>
            <option value="RAIL">RAIL（铁路）</option>
          </select>
        </label>
        <label class="form-field">
          <span>Pickup 日期 <em>必填</em></span>
          <input v-model="form.pickupDate" type="date" readonly />
        </label>
        <label class="form-field">
          <span>Delivery 日期 <em>必填</em></span>
          <input v-model="form.deliveryDate" type="date" readonly />
        </label>
        <label class="form-field">
          <span>贸易条款</span>
          <select v-model="form.incoterm" disabled>
            <option value="FCA">FCA</option>
            <option value="FOB">FOB</option>
            <option value="EXW">EXW</option>
          </select>
        </label>
      </section>

      <section v-else-if="currentStep === 3" class="link-wizard-panel">
        <div v-if="application.bookingDetails" class="feedback-banner success">
          <span>✓</span>
          <p>本步骤已用 PDF 的包组和 Total all 总计初始化，请对照装箱单确认。</p>
        </div>
        <div v-if="packageRows.length" class="pdf-table-scroll">
          <table class="pdf-package-table">
            <thead>
              <tr>
                <th>包</th>
                <th>品名</th>
                <th>托数</th>
                <th>箱数</th>
                <th>数量</th>
                <th>CBM</th>
                <th>毛重 KG</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(packageGroup, index) in packageRows" :key="index">
                <td>包{{ index + 1 }}</td>
                <td>{{ packageGroup.description }}</td>
                <td>{{ packageGroup.pallets }}</td>
                <td>{{ packageGroup.cartons }}</td>
                <td>{{ packageGroup.quantity }}</td>
                <td>{{ packageGroup.cbm ?? '—' }}</td>
                <td>{{ packageGroup.grossWeightKg }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="link-wizard-field-grid">
          <label class="form-field">
            <span>包裹数 <em>必填</em></span>
            <input v-model="form.packageCount" readonly />
          </label>
          <label class="form-field">
            <span>托数 <em>必填</em></span>
            <input v-model="form.palletCount" readonly />
          </label>
          <label class="form-field">
            <span>体积 CBM <em>必填</em></span>
            <input v-model="form.volumeCbm" readonly />
          </label>
          <label class="form-field">
            <span>毛重 KG <em>必填</em></span>
            <input v-model="form.grossWeightKg" readonly />
          </label>
        </div>
        <label class="form-field">
          <span>Package Type</span>
          <select v-model="form.packageType" disabled>
            <option value="PALLET">PALLET</option>
            <option value="CARTON">CARTON</option>
            <option value="CRATE">CRATE</option>
          </select>
        </label>
      </section>

      <section v-else-if="currentStep === 4" class="link-wizard-panel">
        <div class="feedback-banner success">
          <span>✓</span>
          <p>PO 编号和数量优先来自 PDF 包组中的 PO allocation；没有识别到时再人工补充。</p>
        </div>
        <div v-if="poRows.length" class="pdf-table-scroll">
          <table class="pdf-package-table">
            <thead>
              <tr>
                <th>包</th>
                <th>品名</th>
                <th>PO 编号</th>
                <th>分配数量</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in poRows" :key="`${row.packageName}-${row.poNumber}`">
                <td>{{ row.packageName }}</td>
                <td>{{ row.description }}</td>
                <td>{{ row.poNumber }}</td>
                <td>{{ row.quantity ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <label class="form-field">
          <span>PO 总订舱数量</span>
          <input v-model="form.bookingQuantity" readonly />
        </label>
      </section>

      <section v-else-if="currentStep === 5" class="link-wizard-panel">
        <div v-if="application.bookingDetails" class="feedback-banner success">
          <span>✓</span>
          <p>Shipper、Consignee、Notify Party 已从 PDF 识别结果带入，可以人工修正。</p>
        </div>
        <label class="form-field">
          <span>Shipper / 发货人 <em>必填</em></span>
          <textarea v-model="form.shipperName" rows="3" readonly></textarea>
        </label>
        <label class="form-field">
          <span>Consignee / 收货人 <em>必填</em></span>
          <textarea v-model="form.consigneeName" rows="3" readonly></textarea>
        </label>
        <label class="form-field">
          <span>Notify Party / 通知人 <em>必填</em></span>
          <textarea v-model="form.notifyPartyName" rows="3" readonly></textarea>
        </label>
      </section>

      <section v-else-if="currentStep === 6" class="link-wizard-panel">
        <div class="feedback-banner warning">
          <span>!</span>
          <p>机器人只填写附件说明，本地演示不会把文件上传到真实 LINK。</p>
        </div>
        <label class="form-field">
          <span>附件备注（可选）</span>
          <input
            v-model="form.attachmentName"
            readonly
          />
          <small>只记录浏览器演示内容，不上传文件。</small>
        </label>
      </section>

      <section v-else class="link-wizard-panel">
        <div class="feedback-banner warning">
          <span>!</span>
          <p>机器人已经自动 SAVE。请业务人员核对 Recap，确认无误后点击 VALIDATE。</p>
        </div>
        <dl class="link-wizard-recap">
          <div v-if="application.bookingDetails">
            <dt>PDF Source</dt>
            <dd>
              Invoice {{ application.bookingDetails.invoiceNumber || '—' }} ·
              CIQ {{ application.bookingDetails.ciqNumber || '—' }} ·
              {{ application.bookingDetails.packages.length }} 个包组
            </dd>
          </div>
          <div><dt>Destination</dt><dd>{{ form.originPort }} → {{ form.destinationPort }} · {{ form.transportMode }} · {{ form.incoterm }}</dd></div>
          <div><dt>日期</dt><dd>Pickup {{ form.pickupDate }} · Delivery {{ form.deliveryDate }}</dd></div>
          <div><dt>Package</dt><dd>{{ form.commodityName }} · {{ form.packageType }} · {{ form.packageCount }} 件 / {{ form.palletCount }} 托 · {{ form.volumeCbm }} CBM · {{ form.grossWeightKg }} KG</dd></div>
          <div><dt>Purchase Order</dt><dd>{{ form.purchaseOrderNumber }} · 数量 {{ form.bookingQuantity }}</dd></div>
          <div><dt>Participants</dt><dd>{{ form.shipperName }} → {{ form.consigneeName }} · Notify {{ form.notifyPartyName }}</dd></div>
          <div><dt>Attachment</dt><dd>{{ form.attachmentName || '本流程跳过附件上传' }}</dd></div>
        </dl>
      </section>

      <div v-if="validationError" class="inline-error" role="alert">{{ validationError }}</div>
      <div v-if="saveMessage" class="feedback-banner success" role="status">
        <span>✓</span>
        <p>{{ saveMessage }}</p>
      </div>

      <div class="drawer-actions link-wizard-actions">
        <button
          class="secondary-button"
          type="button"
          :disabled="currentStep === 1 || submitting || autoFilling"
          @click="previous"
        >
          上一步
        </button>
        <button
          v-if="currentStep < 7"
          class="primary-button"
          type="button"
          :disabled="submitting || autoFilling"
          @click="next"
        >
          下一步
        </button>
        <template v-else>
          <button
            class="primary-button"
            type="button"
            :disabled="submitting || autoFilling || !saved"
            @click="validateAndSubmit"
          >
            {{ submitting ? '正在 VALIDATE…' : '业务确认：VALIDATE 并提交' }}
          </button>
        </template>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.link-automation-progress {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 0 20px 16px;
  padding: 12px 14px;
  border: 1px solid #b9d6ca;
  border-radius: 12px;
  background: #eef8f3;
}

.link-automation-progress > span {
  min-width: 48px;
  padding: 5px 8px;
  border-radius: 999px;
  background: #267257;
  color: white;
  text-align: center;
  font-size: 11px;
  font-weight: 800;
}

.link-automation-progress p {
  margin: 3px 0 0;
  color: var(--subtle);
}

.link-automation-progress.complete {
  border-color: #8fc8ad;
  background: #e5f6ed;
}
</style>
