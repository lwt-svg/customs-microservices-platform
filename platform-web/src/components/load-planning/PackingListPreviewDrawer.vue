<script setup lang="ts">
import { ref, watch } from 'vue'

import {
  previewLoadPlanningPackingList,
  type PackingListPreview,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  apply: [payload: { filename: string; preview: PackingListPreview }]
}>()

type PreviewStep = 'selecting' | 'uploading' | 'reviewing'

const step = ref<PreviewStep>('selecting')
const selectedFile = ref<File | null>(null)
const preview = ref<PackingListPreview | null>(null)
const errorMessage = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
)

function reset(): void {
  step.value = 'selecting'
  selectedFile.value = null
  preview.value = null
  errorMessage.value = ''
  if (fileInput.value) fileInput.value.value = ''
}

function close(): void {
  if (step.value !== 'uploading') {
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

async function upload(): Promise<void> {
  const file = selectedFile.value
  if (!file) {
    errorMessage.value = '请先选择一份带文字层的 Packing List PDF。'
    return
  }

  errorMessage.value = validateFile(file)
  if (errorMessage.value) return

  step.value = 'uploading'
  try {
    preview.value = await previewLoadPlanningPackingList(file)
    step.value = 'reviewing'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'PL 上传解析失败'
    step.value = 'selecting'
  }
}

function applyToForm(): void {
  if (!selectedFile.value || !preview.value) return
  emit('apply', {
    filename: selectedFile.value.name,
    preview: preview.value,
  })
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  return `${(value / 1024).toFixed(1)} KB`
}

function displayValue(value: string | number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${value}${suffix}`
}

function formatConfidence(value: number): string {
  return `${Math.round(value * 100)}%`
}
</script>

<template>
  <div v-if="modelValue" class="drawer-backdrop" @click.self="close">
    <aside class="form-drawer pl-preview-drawer" aria-labelledby="pl-preview-title">
      <div class="drawer-header">
        <div>
          <h3 id="pl-preview-title">从 PL 识别箱规与历史装托模式</h3>
          <p>文件只在内存中解析；本步骤不保存 PDF，也不会自动创建任务。</p>
        </div>
        <button
          class="close-button"
          type="button"
          aria-label="关闭"
          :disabled="step === 'uploading'"
          @click="close"
        >
          <UiIcon name="close" />
        </button>
      </div>

      <div class="pl-step-strip" aria-label="PL 导入步骤">
        <span :class="{ active: step === 'selecting' || step === 'uploading' }">1 选择并解析</span>
        <i></i>
        <span :class="{ active: step === 'reviewing' }">2 查看证据</span>
        <i></i>
        <span>3 带入后人工补齐</span>
      </div>

      <div v-if="step === 'selecting' || step === 'uploading'" class="pl-select-panel">
        <input
          ref="fileInput"
          class="pl-visually-hidden"
          type="file"
          accept=".pdf,application/pdf"
          @change="onFileChanged"
        />
        <button
          class="pl-drop-zone"
          type="button"
          :disabled="step === 'uploading'"
          @click="chooseFile"
        >
          <UiIcon name="upload" />
          <strong>{{ selectedFile ? selectedFile.name : '选择 Packing List PDF' }}</strong>
          <span v-if="selectedFile">{{ formatBytes(selectedFile.size) }}</span>
          <span v-else>只支持带文字层的 PDF，最大 5MB；当前不支持扫描图片 OCR</span>
        </button>

        <div class="feedback-banner warning">
          <span>!</span>
          <p>
            系统会提取箱规、数量和历史装托记录供你复核；识别结果不会直接作为正式装载方案。
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

      <div v-else-if="preview && selectedFile" class="pl-review">
        <div class="pl-file-summary">
          <UiIcon name="file" />
          <div>
            <strong>{{ selectedFile.name }}</strong>
            <span>{{ formatBytes(selectedFile.size) }} · 识别完成</span>
          </div>
        </div>

        <div class="feedback-banner warning">
          <span>!</span>
          <p>
            必须人工复核后才能带入创建表单。带入只会填表，不会自动提交或创建任务；
            托盘空托高度、最大载重等未知安全参数不会猜测。
          </p>
        </div>

        <section class="pl-review-section">
          <div class="pl-review-heading">
            <div>
              <span>单据摘要</span>
              <h4>文档声明与识别范围</h4>
            </div>
            <b>{{ preview.cargoCandidates.length }} 种箱规候选</b>
          </div>
          <div class="pl-summary-grid">
            <dl>
              <dt>声明总托数</dt>
              <dd>{{ displayValue(preview.declaredTotalPallets, ' 托') }}</dd>
            </dl>
            <dl>
              <dt>声明总体积</dt>
              <dd>{{ displayValue(preview.declaredTotalCbm, ' CBM') }}</dd>
            </dl>
            <dl>
              <dt>历史模式行</dt>
              <dd>{{ preview.historicalPalletPatterns.length }} 行</dd>
            </dl>
          </div>
        </section>

        <section class="pl-review-section">
          <div class="pl-review-heading">
            <div>
              <span>箱规识别</span>
              <h4>按箱规聚合的货物候选</h4>
            </div>
            <b>{{ preview.cargoCandidates.length }} 种</b>
          </div>
          <div class="pl-table-scroll">
            <table class="pl-preview-table cargo">
              <thead>
                <tr>
                  <th>代表 Item</th>
                  <th>同箱规 Items</th>
                  <th>描述</th>
                  <th>箱数</th>
                  <th>件/箱</th>
                  <th>单箱净重</th>
                  <th>单箱毛重</th>
                  <th>长×宽×高</th>
                  <th>来源坐标</th>
                  <th>置信度</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="candidate in preview.cargoCandidates" :key="candidate.cargoKey">
                  <td>{{ candidate.itemCode }}</td>
                  <td>{{ candidate.itemCodes.join('、') }}</td>
                  <td>{{ candidate.description }}</td>
                  <td>{{ candidate.cartonQuantity }}</td>
                  <td>{{ candidate.unitsPerCarton }}</td>
                  <td>{{ candidate.cartonNetWeightKg }} kg</td>
                  <td>{{ candidate.cartonGrossWeightKg }} kg</td>
                  <td>
                    {{ candidate.lengthMm }} × {{ candidate.widthMm }} ×
                    {{ candidate.heightMm }} mm
                  </td>
                  <td>{{ candidate.sourceLocator }}</td>
                  <td>{{ formatConfidence(candidate.confidence) }}</td>
                </tr>
                <tr v-if="preview.cargoCandidates.length === 0">
                  <td colspan="10">没有识别到可带入的箱规候选。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="pl-review-section">
          <div class="pl-review-heading">
            <div>
              <span>历史装托</span>
              <h4>文件中明确记载的历史装托模式</h4>
            </div>
            <b>{{ preview.historicalPalletPatterns.length }} 行</b>
          </div>
          <div class="pl-table-scroll">
            <table class="pl-preview-table">
              <thead>
                <tr>
                  <th>cargoKey</th>
                  <th>每层箱数</th>
                  <th>模式箱数</th>
                  <th>覆盖托数</th>
                  <th>托盘皮重</th>
                  <th>历史成托长×宽×高</th>
                  <th>CBM</th>
                  <th>来源坐标</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(pattern, index) in preview.historicalPalletPatterns"
                  :key="`${pattern.cargoKey}-${pattern.sourceLocator}-${index}`"
                >
                  <td>{{ displayValue(pattern.cargoKey) }}</td>
                  <td>{{ pattern.cartonsPerLayer }}</td>
                  <td>{{ pattern.patternCartonCount }}</td>
                  <td>{{ displayValue(pattern.palletQuantity) }}</td>
                  <td>{{ pattern.palletTareWeightKg }} kg</td>
                  <td>
                    {{ pattern.loadedLengthMm }} × {{ pattern.loadedWidthMm }} ×
                    {{ pattern.loadedHeightMm }} mm
                  </td>
                  <td>{{ pattern.cbm }}</td>
                  <td>{{ pattern.sourceLocator }}</td>
                </tr>
                <tr v-if="preview.historicalPalletPatterns.length === 0">
                  <td colspan="8">没有识别到历史装托模式。</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="pl-safety-note">
            注意：历史 loadedHeightMm 只用于展示和对账，不会写入“空托高度”或“最大成托高度”。
          </p>
        </section>

        <section class="pl-review-section">
          <div class="pl-review-heading">
            <div>
              <span>识别检查</span>
              <h4>解析器对账与人工复核提示</h4>
            </div>
            <b>{{ preview.checks.length }} 条</b>
          </div>
          <ul v-if="preview.checks.length" class="pl-check-list">
            <li v-for="check in preview.checks" :key="check">{{ check }}</li>
          </ul>
          <p v-else class="pl-empty-note">解析器没有返回额外检查项。</p>
        </section>

        <div class="drawer-actions pl-sticky-actions">
          <button class="secondary-button" type="button" @click="reset">重新选择</button>
          <button
            class="primary-button"
            type="button"
            :disabled="preview.cargoCandidates.length === 0"
            @click="applyToForm"
          >
            带入创建表单（不提交）
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.pl-preview-drawer {
  width: min(1240px, calc(100vw - 32px));
  padding-bottom: 0;
}

.pl-step-strip {
  display: grid;
  grid-template-columns: auto 1fr auto 1fr auto;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
  padding: 12px 15px;
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--subtle);
  font-size: 9px;
}

.pl-step-strip span.active {
  color: var(--accent);
  font-weight: 700;
}

.pl-step-strip i {
  height: 1px;
  background: var(--border);
}

.pl-select-panel,
.pl-review {
  display: grid;
  gap: 16px;
}

.pl-drop-zone {
  display: grid;
  min-height: 190px;
  place-items: center;
  gap: 8px;
  padding: 24px;
  border: 1px dashed #b8c9c2;
  border-radius: 12px;
  color: var(--accent);
  background: #f7fbf9;
  cursor: pointer;
}

.pl-drop-zone .ui-icon {
  width: 34px;
  height: 34px;
}

.pl-drop-zone span {
  color: var(--muted);
  font-size: 10px;
}

.pl-file-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-soft);
}

.pl-file-summary > div {
  display: grid;
  gap: 4px;
}

.pl-file-summary strong {
  font-size: 11px;
}

.pl-file-summary span {
  color: var(--muted);
  font-size: 9px;
}

.pl-review-section {
  display: grid;
  gap: 13px;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
}

.pl-review-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.pl-review-heading > div {
  display: grid;
  gap: 3px;
}

.pl-review-heading span {
  color: var(--accent);
  font-size: 8px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.pl-review-heading h4 {
  margin: 0;
  font-size: 13px;
}

.pl-review-heading > b {
  padding: 4px 9px;
  border-radius: 999px;
  color: #356c50;
  background: var(--green-soft);
  font-size: 8px;
  white-space: nowrap;
}

.pl-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.pl-summary-grid dl {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 12px;
  border-radius: 9px;
  background: var(--surface-soft);
}

.pl-summary-grid dt {
  color: var(--muted);
  font-size: 8px;
}

.pl-summary-grid dd {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}

.pl-table-scroll {
  overflow-x: auto;
}

.pl-preview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 9px;
  white-space: nowrap;
}

.pl-preview-table th,
.pl-preview-table td {
  padding: 9px 10px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}

.pl-preview-table th {
  color: var(--muted);
  background: var(--surface-soft);
  font-weight: 700;
}

.pl-preview-table.cargo td:nth-child(2),
.pl-preview-table.cargo td:nth-child(3) {
  min-width: 180px;
  white-space: normal;
  line-height: 1.5;
}

.pl-safety-note,
.pl-empty-note {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  color: #80672e;
  background: #fffbef;
  font-size: 9px;
  line-height: 1.6;
}

.pl-check-list {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 12px 12px 12px 30px;
  border-radius: 8px;
  color: #356c50;
  background: var(--green-soft);
  font-size: 9px;
  line-height: 1.5;
}

.pl-sticky-actions {
  position: sticky;
  bottom: 0;
  margin: 0 -27px;
  padding: 17px 27px 27px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(8px);
}

.pl-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}

@media (max-width: 760px) {
  .pl-step-strip {
    grid-template-columns: 1fr;
  }

  .pl-step-strip i {
    display: none;
  }

  .pl-summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
