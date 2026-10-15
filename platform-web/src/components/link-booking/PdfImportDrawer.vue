<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import {
  confirmLinkBookingDocumentImport,
  uploadLinkBookingDocument,
  type LinkBookingApplication,
  type LinkBookingDocumentImport,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [application: LinkBookingApplication]
}>()

type ImportStep = 'selecting' | 'uploading' | 'reviewing' | 'confirming' | 'done'

const DEFAULT_ORIGIN = 'CHINA - CN · Fengxian - CNFGX'
const DEFAULT_DESTINATION = 'FRANCE - FR · LE HAVRE(76) - FRLEH'

const step = ref<ImportStep>('selecting')
const selectedFile = ref<File | null>(null)
const documentImport = ref<LinkBookingDocumentImport | null>(null)
const errorMessage = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const form = reactive({
  applicationNumber: '',
  originPort: '',
  destinationPort: '',
  transportMode: 'SEA',
  containerType: '40HQ',
  requestedEtd: '',
})

const bookingDetails = computed(() => documentImport.value?.candidates.bookingDetails ?? null)

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
)

function reset(): void {
  step.value = 'selecting'
  selectedFile.value = null
  documentImport.value = null
  errorMessage.value = ''
  form.applicationNumber = ''
  form.originPort = ''
  form.destinationPort = ''
  form.transportMode = 'SEA'
  form.containerType = '40HQ'
  form.requestedEtd = ''
  if (fileInput.value) fileInput.value.value = ''
}

function close(): void {
  if (step.value !== 'uploading' && step.value !== 'confirming') {
    emit('update:modelValue', false)
  }
}

function chooseFile(): void {
  fileInput.value?.click()
}

function onFileChanged(event: Event): void {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
  errorMessage.value = ''
}

function validateFile(file: File): string {
  if (!file.name.toLowerCase().endsWith('.pdf')) return '请选择扩展名为 .pdf 的文件。'
  if (file.size === 0) return 'PDF 文件不能为空。'
  if (file.size > 5 * 1024 * 1024) return 'PDF 不能超过 5MB。'
  return ''
}

function copyCandidates(result: LinkBookingDocumentImport): void {
  const hasPackingListDetails = Boolean(result.candidates.bookingDetails)
  form.applicationNumber = result.candidates.applicationNumber.value ?? ''
  form.originPort =
    result.candidates.originPort.value ?? (hasPackingListDetails ? DEFAULT_ORIGIN : '')
  form.destinationPort =
    result.candidates.destinationPort.value ?? (hasPackingListDetails ? DEFAULT_DESTINATION : '')
  form.transportMode = result.candidates.transportMode.value || 'SEA'
  form.containerType = result.candidates.containerType.value || '40HQ'
  form.requestedEtd = result.candidates.requestedEtd.value ?? ''
}

async function upload(): Promise<void> {
  const file = selectedFile.value
  if (!file) {
    errorMessage.value = '请先选择一份文本型装箱单 PDF。'
    return
  }
  errorMessage.value = validateFile(file)
  if (errorMessage.value) return

  step.value = 'uploading'
  try {
    const result = await uploadLinkBookingDocument(file)
    documentImport.value = result
    copyCandidates(result)
    step.value = 'reviewing'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'PDF 上传解析失败'
    step.value = 'selecting'
  }
}

function validateReview(): string {
  if (!/^DEMO-[A-Za-z0-9_-]+$/.test(form.applicationNumber.trim())) {
    return '申请编号必须以 DEMO- 开头，且只能包含字母、数字、下划线和短横线。'
  }
  if (!form.originPort.trim()) return '请确认起运港。'
  if (!form.destinationPort.trim()) return '请确认目的港。'
  if (!form.transportMode.trim()) return '请确认运输方式。'
  if (!form.containerType.trim()) return '请确认箱型。'
  if (!form.requestedEtd) return '请确认计划离港日期，模拟 LINK 自动填单会用它填写日期步骤。'
  return ''
}

async function confirm(): Promise<void> {
  if (!documentImport.value) return
  errorMessage.value = validateReview()
  if (errorMessage.value) return

  step.value = 'confirming'
  try {
    const created = await confirmLinkBookingDocumentImport(documentImport.value.id, {
      applicationNumber: form.applicationNumber.trim(),
      originPort: form.originPort.trim(),
      destinationPort: form.destinationPort.trim(),
      transportMode: form.transportMode.trim(),
      containerType: form.containerType.trim(),
      requestedEtd: form.requestedEtd || null,
      bookingDetails: bookingDetails.value,
    })
    step.value = 'done'
    emit('created', created)
    emit('update:modelValue', false)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '人工确认创建草稿失败'
    // 409 等错误不能清空用户已经修改的内容，方便修正编号后再次提交。
    step.value = 'reviewing'
  }
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  return `${(value / 1024).toFixed(1)} KB`
}

function displayValue(value: string | number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${value}${suffix}`
}

function poAllocationText(
  allocations: Array<{ poNumber: string; quantity: number | null }>,
): string {
  if (!allocations.length) return '—'
  return allocations
    .map((allocation) => `${allocation.poNumber}:${displayValue(allocation.quantity)}`)
    .join('；')
}
</script>

<template>
  <div v-if="modelValue" class="drawer-backdrop" @click.self="close">
    <aside class="form-drawer pdf-import-drawer" aria-labelledby="pdf-import-title">
      <div class="drawer-header">
        <div>
          <h3 id="pdf-import-title">从 PDF 导入订舱草稿</h3>
          <p>解析结果只是候选；人工检查并确认后才会创建 DRAFT。</p>
        </div>
        <button
          class="close-button"
          type="button"
          aria-label="关闭"
          :disabled="step === 'uploading' || step === 'confirming'"
          @click="close"
        >
          <UiIcon name="close" />
        </button>
      </div>

      <div class="pdf-step-strip" aria-label="导入步骤">
        <span :class="{ active: step === 'selecting' || step === 'uploading' }">1 选择并解析</span>
        <i></i>
        <span :class="{ active: step === 'reviewing' || step === 'confirming' }">2 人工复核</span>
        <i></i>
        <span :class="{ active: step === 'done' }">3 创建草稿</span>
      </div>

      <div v-if="step === 'selecting' || step === 'uploading'" class="pdf-select-panel">
        <input
          ref="fileInput"
          class="visually-hidden"
          type="file"
          accept=".pdf,application/pdf"
          @change="onFileChanged"
        />
        <button class="pdf-drop-zone" type="button" :disabled="step === 'uploading'" @click="chooseFile">
          <UiIcon name="upload" />
          <strong>{{ selectedFile ? selectedFile.name : '选择一份文本型装箱单 PDF' }}</strong>
          <span v-if="selectedFile">{{ formatBytes(selectedFile.size) }}</span>
          <span v-else>只支持 PDF，最大 5MB，不支持扫描图片和加密文件</span>
        </button>

        <div class="feedback-banner warning">
          <span>!</span>
          <p>
            文件仅保存在本机 Git 忽略目录，不会提交到代码仓库；申请编号必须使用 DEMO- 前缀。
          </p>
        </div>

        <div v-if="errorMessage" class="inline-error" role="alert">{{ errorMessage }}</div>

        <div class="drawer-actions">
          <button class="secondary-button" type="button" :disabled="step === 'uploading'" @click="close">
            取消
          </button>
          <button class="primary-button" type="button" :disabled="step === 'uploading'" @click="upload">
            {{ step === 'uploading' ? '正在上传并解析…' : '上传并解析' }}
          </button>
        </div>
      </div>

      <form v-else-if="documentImport" class="pdf-review-form" @submit.prevent="confirm">
        <div class="pdf-file-summary">
          <UiIcon name="file" />
          <div>
            <strong>{{ documentImport.originalFilename }}</strong>
            <span>{{ formatBytes(documentImport.fileSizeBytes) }} · {{ documentImport.parserVersion }}</span>
          </div>
        </div>

        <div class="feedback-banner warning">
          <span>!</span>
          <p>请先核对 PDF 识别信息和包组，再补充订舱业务字段。业务补充项不是 PDF 识别结果。</p>
        </div>

        <section class="pdf-review-section">
          <div class="pdf-review-section-heading">
            <div>
              <span>PDF 识别信息</span>
              <h4>装箱单抬头、参与方与总计</h4>
            </div>
            <b>{{ bookingDetails ? '已结构化' : '未识别' }}</b>
          </div>

          <div v-if="bookingDetails" class="pdf-details-grid">
            <dl><dt>Invoice No.</dt><dd>{{ displayValue(bookingDetails.invoiceNumber) }}</dd></dl>
            <dl><dt>Document Date</dt><dd>{{ displayValue(bookingDetails.documentDate) }}</dd></dl>
            <dl><dt>CIQ</dt><dd>{{ displayValue(bookingDetails.ciqNumber) }}</dd></dl>
            <dl class="wide"><dt>Shipper</dt><dd>{{ displayValue(bookingDetails.shipper) }}</dd></dl>
            <dl class="wide"><dt>Consignee</dt><dd>{{ displayValue(bookingDetails.consignee) }}</dd></dl>
            <dl class="wide"><dt>Notify Party</dt><dd>{{ displayValue(bookingDetails.notifyParty) }}</dd></dl>
            <dl class="wide"><dt>Marks</dt><dd>{{ displayValue(bookingDetails.marks) }}</dd></dl>
          </div>
          <p v-else class="pdf-empty-details">
            当前解析响应没有装箱单明细。请确认 Python 服务已经更新后重新上传。
          </p>

          <div v-if="bookingDetails" class="pdf-total-grid">
            <span><b>{{ displayValue(bookingDetails.totalPallets) }}</b>托</span>
            <span><b>{{ displayValue(bookingDetails.totalCartons) }}</b>箱</span>
            <span><b>{{ displayValue(bookingDetails.totalQuantity) }}</b>总数量</span>
            <span><b>{{ displayValue(bookingDetails.totalCbm) }}</b>CBM</span>
            <span><b>{{ displayValue(bookingDetails.totalNetWeightKg) }}</b>净重 KG</span>
            <span><b>{{ displayValue(bookingDetails.totalGrossWeightKg) }}</b>毛重 KG</span>
          </div>

          <div v-if="bookingDetails?.checks.length" class="pdf-check-list">
            <strong>自动对账</strong>
            <ul>
              <li v-for="check in bookingDetails.checks" :key="check">{{ check }}</li>
            </ul>
          </div>
        </section>

        <section class="pdf-review-section">
          <div class="pdf-review-section-heading">
            <div>
              <span>包组表格</span>
              <h4>Package 与 PO 分配</h4>
            </div>
            <b>{{ bookingDetails?.packages.length ?? 0 }} 组</b>
          </div>
          <div class="pdf-table-scroll">
            <table class="pdf-package-table">
              <thead>
                <tr>
                  <th>包</th>
                  <th>品名</th>
                  <th>托数</th>
                  <th>箱数</th>
                  <th>数量</th>
                  <th>CBM</th>
                  <th>净重</th>
                  <th>毛重</th>
                  <th>PO 分配</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(packageGroup, index) in bookingDetails?.packages ?? []" :key="index">
                  <td>包{{ index + 1 }}</td>
                  <td>{{ displayValue(packageGroup.description) }}</td>
                  <td>{{ displayValue(packageGroup.pallets) }}</td>
                  <td>{{ displayValue(packageGroup.cartons) }}</td>
                  <td>{{ displayValue(packageGroup.quantity) }}</td>
                  <td>{{ displayValue(packageGroup.cbm) }}</td>
                  <td>{{ displayValue(packageGroup.netWeightKg) }}</td>
                  <td>{{ displayValue(packageGroup.grossWeightKg) }}</td>
                  <td>{{ poAllocationText(packageGroup.poAllocations) }}</td>
                </tr>
                <tr v-if="!bookingDetails?.packages.length">
                  <td colspan="9">没有识别到包组。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="bookingDetails?.items.length" class="pdf-review-section">
          <div class="pdf-review-section-heading">
            <div>
              <span>商品明细</span>
              <h4>Item / Lot / PO</h4>
            </div>
            <b>{{ bookingDetails.items.length }} 行</b>
          </div>
          <div class="pdf-table-scroll">
            <table class="pdf-package-table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Lot</th>
                  <th>PO</th>
                  <th>品名</th>
                  <th>数量</th>
                  <th>箱数</th>
                  <th>净重</th>
                  <th>毛重</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in bookingDetails.items" :key="`${item.itemCode}-${index}`">
                  <td>{{ displayValue(item.itemCode) }}</td>
                  <td>{{ displayValue(item.lotNumber) }}</td>
                  <td>{{ displayValue(item.poNumber) }}</td>
                  <td>{{ displayValue(item.description) }}</td>
                  <td>{{ displayValue(item.quantity) }}</td>
                  <td>{{ displayValue(item.cartons) }}</td>
                  <td>{{ displayValue(item.netWeightKg) }}</td>
                  <td>{{ displayValue(item.grossWeightKg) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="pdf-review-section">
          <div class="pdf-review-section-heading">
            <div>
              <span>业务补充字段</span>
              <h4>创建订舱草稿所需信息</h4>
            </div>
            <b>人工确认</b>
          </div>
          <p class="pdf-business-note">
            起运地、目的地、运输方式、箱型和计划离港日不是本装箱单的可靠字段，请按实际业务填写。
          </p>
          <div class="pdf-business-fields">
            <label class="form-field">
              <span>申请编号 <em>必填</em></span>
              <input v-model="form.applicationNumber" maxlength="64" autocomplete="off" />
              <small>若 PDF 有 Invoice Number，会自动派生 DEMO-PL-...；它不是 PDF 原文申请号。</small>
            </label>
            <label class="form-field">
              <span>起运地（业务固定/人工补充）<em>必填</em></span>
              <input v-model="form.originPort" maxlength="64" autocomplete="off" />
              <small>当前学习路线默认值，不是 PDF 识别结果。</small>
            </label>
            <label class="form-field">
              <span>目的地（业务固定/人工补充）<em>必填</em></span>
              <input v-model="form.destinationPort" maxlength="64" autocomplete="off" />
              <small>当前学习路线默认值，不是 PDF 识别结果。</small>
            </label>
            <label class="form-field">
              <span>运输方式（人工补充）<em>必填</em></span>
              <select v-model="form.transportMode">
            <option value="SEA">SEA（海运）</option>
            <option value="AIR">AIR（空运）</option>
            <option value="RAIL">RAIL（铁路）</option>
          </select>
            </label>
            <label class="form-field">
              <span>箱型（人工补充）<em>必填</em></span>
              <select v-model="form.containerType">
                <option value="20GP">20GP</option>
                <option value="40GP">40GP</option>
                <option value="40HQ">40HQ</option>
              </select>
            </label>
            <label class="form-field">
              <span>计划离港日期（人工补充）<em>必填</em></span>
              <input v-model="form.requestedEtd" type="date" />
              <small>自动填单演示会用该日期初始化 Pickup 和 Delivery，请按业务实际确认。</small>
            </label>
          </div>
        </section>

        <div v-if="errorMessage" class="inline-error" role="alert">{{ errorMessage }}</div>

        <div class="drawer-actions">
          <button
            class="secondary-button"
            type="button"
            :disabled="step === 'confirming'"
            @click="reset"
          >
            重新选择
          </button>
          <button class="primary-button" type="submit" :disabled="step === 'confirming'">
            {{ step === 'confirming' ? '正在创建草稿…' : '确认字段并创建草稿' }}
          </button>
        </div>
      </form>
    </aside>
  </div>
</template>

<style scoped>
.pdf-import-drawer {
  width: min(1180px, calc(100vw - 32px));
}

.pdf-step-strip {
  display: grid;
  grid-template-columns: auto 1fr auto 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  color: var(--subtle);
  font-size: 10px;
}

.pdf-step-strip span.active {
  color: var(--accent);
  font-weight: 700;
}

.pdf-step-strip i {
  height: 1px;
  background: var(--border);
}

.pdf-select-panel,
.pdf-review-form {
  display: grid;
  gap: 16px;
  padding: 20px;
  overflow-y: auto;
}

.pdf-drop-zone {
  display: grid;
  min-height: 180px;
  place-items: center;
  gap: 8px;
  padding: 24px;
  border: 1px dashed #b8c9c2;
  border-radius: 12px;
  color: var(--accent);
  background: #f7fbf9;
  cursor: pointer;
}

.pdf-drop-zone .ui-icon {
  width: 34px;
  height: 34px;
}

.pdf-drop-zone span {
  color: var(--muted);
  font-size: 10px;
}

.pdf-file-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-soft);
}

.pdf-file-summary > div {
  display: grid;
  gap: 4px;
}

.pdf-file-summary strong {
  font-size: 11px;
}

.pdf-file-summary span {
  color: var(--muted);
  font-size: 9px;
}

.pdf-review-section {
  display: grid;
  gap: 14px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
}

.pdf-review-section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.pdf-review-section-heading > div {
  display: grid;
  gap: 3px;
}

.pdf-review-section-heading span {
  color: var(--accent);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.pdf-review-section-heading h4 {
  margin: 0;
  font-size: 13px;
}

.pdf-review-section-heading > b {
  padding: 4px 9px;
  border-radius: 999px;
  color: #356c50;
  background: var(--green-soft);
  font-size: 8px;
}

.pdf-details-grid,
.pdf-total-grid,
.pdf-business-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.pdf-details-grid dl {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  background: var(--surface-soft);
}

.pdf-details-grid dl.wide {
  grid-column: span 3;
}

.pdf-details-grid dt {
  color: var(--muted);
  font-size: 8px;
  font-weight: 700;
}

.pdf-details-grid dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 10px;
  line-height: 1.5;
}

.pdf-total-grid span {
  display: grid;
  gap: 3px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--muted);
  font-size: 8px;
}

.pdf-total-grid b {
  color: var(--text);
  font-size: 13px;
}

.pdf-check-list {
  padding: 10px 12px;
  border-radius: 8px;
  color: #356c50;
  background: var(--green-soft);
  font-size: 9px;
}

.pdf-check-list ul {
  display: grid;
  gap: 4px;
  margin: 7px 0 0;
  padding-left: 18px;
}

.pdf-table-scroll {
  overflow-x: auto;
}

.pdf-package-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 9px;
  white-space: nowrap;
}

.pdf-package-table th,
.pdf-package-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}

.pdf-package-table th {
  color: var(--muted);
  background: var(--surface-soft);
  font-weight: 700;
}

.pdf-package-table td:nth-child(2) {
  min-width: 220px;
  white-space: normal;
}

.pdf-business-note,
.pdf-empty-details {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  color: #80672e;
  background: #fffbef;
  font-size: 9px;
  line-height: 1.5;
}

@media (max-width: 760px) {
  .pdf-details-grid,
  .pdf-total-grid,
  .pdf-business-fields {
    grid-template-columns: 1fr;
  }

  .pdf-details-grid dl.wide {
    grid-column: auto;
  }
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
