<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  createLoadPlanningTask,
  getActiveLoadPlanningAgentRuns,
  getLoadPlanningAgentRunHistory,
  getLoadPlanningTask,
  getLoadPlanningTasks,
  getLoadPlanVersions,
  type CreateLoadPlanningTaskInput,
  type HistoricalPalletPattern,
  type LoadPlanningAgentCurrentStep,
  type LoadPlanningAgentStatus,
  type LoadPlanningSourceType,
  type PackingListPreview,
  type LoadPlanningTask,
  type LoadPlanningTaskSummary,
} from '@/api/platform'
import UiIcon from '@/components/UiIcon.vue'
import PackingListPreviewDrawer from '@/components/load-planning/PackingListPreviewDrawer.vue'

const route = useRoute()

// Three.js 只在用户真正打开智能体工作台时下载，日常任务列表不承担 3D 运行时体积。
const AgentPlanningWorkbench = defineAsyncComponent(
  () => import('@/components/load-planning/AgentPlanningWorkbench.vue'),
)

type NumberDraft = number | null | ''
type OptionalBoolean = boolean | null

interface SourceDraft {
  sourceType: LoadPlanningSourceType
  sourceReference: string
  sourceLocator: string
  confidence: NumberDraft
  verified: boolean
}

interface CargoDraft extends SourceDraft {
  itemCode: string
  skuCode: string
  cargoName: string
  quantity: NumberDraft
  lengthMm: NumberDraft
  widthMm: NumberDraft
  heightMm: NumberDraft
  grossWeightKg: NumberDraft
  maxStackLayers: NumberDraft
  maxTopLoadKg: NumberDraft
  stackable: OptionalBoolean
  keepUpright: OptionalBoolean
  fragile: OptionalBoolean
}

interface PalletDraft extends SourceDraft {
  palletCode: string
  palletName: string
  quantity: NumberDraft
  lengthMm: NumberDraft
  widthMm: NumberDraft
  heightMm: NumberDraft
  tareWeightKg: NumberDraft
  maxLoadKg: NumberDraft
  maxTotalHeightMm: NumberDraft
}

interface TaskDraft {
  taskNumber: string
  taskName: string
  description: string
  cargoItems: CargoDraft[]
  palletSpecs: PalletDraft[]
}

type TaskStageCode =
  | 'PENDING'
  | 'AWAITING_INPUT'
  | 'READY_FOR_REVIEW'
  | 'AWAITING_RELEASE'
  | 'REPLAN_REQUIRED'
  | 'FAILED'
  | 'LOCKED'
  | 'UNAVAILABLE'

type TaskFilterCode = 'ALL' | 'ACTION_REQUIRED' | 'EXCEPTION' | TaskStageCode

interface TaskOperationalState {
  stage: TaskStageCode
  activeRunStatus: LoadPlanningAgentStatus | null
  latestRunStatus: LoadPlanningAgentStatus | null
  currentStep: LoadPlanningAgentCurrentStep | null
  runVersion: number | null
  pendingQuestionCount: number
  candidatePlanCount: number
  selectedPlanCode: string | null
  failureMessage: string | null
  formalVersionNumber: number | null
  formalPlanCode: string | null
  formalLockedAt: string | null
}

const sourceOptions: Array<{ value: LoadPlanningSourceType; label: string }> = [
  { value: 'SYNTHETIC_DEMO', label: '测试 / 演练数据' },
  { value: 'MANUAL', label: '人工录入' },
  { value: 'ENTERPRISE_HISTORICAL', label: '企业历史资料' },
  { value: 'DOCUMENT_IMPORT', label: '本次文档导入' },
]

const tasks = ref<LoadPlanningTaskSummary[]>([])
const loading = ref(true)
const loadError = ref('')
const successMessage = ref('')
const showCreateDrawer = ref(false)
const showPackingListPreview = ref(false)
const submitting = ref(false)
const formError = ref('')
const showDetailDrawer = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref<LoadPlanningTask | null>(null)
const showAgentWorkbench = ref(false)
const agentTask = ref<LoadPlanningTask | null>(null)
const planningTaskNumber = ref('')
const keyword = ref('')
const stageFilter = ref<TaskFilterCode>('ALL')
const taskOperations = ref<Record<string, TaskOperationalState>>({})
const lastRefreshedAt = ref<Date | null>(null)

const totalBoxes = computed(() =>
  tasks.value.reduce((total, task) => total + task.cargoBoxCount, 0),
)

const actionRequiredTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter(
      (state) =>
        state.stage === 'AWAITING_INPUT' ||
        state.stage === 'READY_FOR_REVIEW' ||
        state.stage === 'AWAITING_RELEASE' ||
        state.stage === 'REPLAN_REQUIRED' ||
        state.stage === 'FAILED' ||
        state.stage === 'UNAVAILABLE',
    ).length,
)

const lockedTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter((state) => state.formalVersionNumber !== null)
      .length,
)

const awaitingInputTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter((state) => state.stage === 'AWAITING_INPUT')
      .length,
)

const awaitingReviewTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter((state) => state.stage === 'READY_FOR_REVIEW')
      .length,
)

const awaitingReleaseTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter((state) => state.stage === 'AWAITING_RELEASE')
      .length,
)

const exceptionTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter(
      (state) =>
        state.stage === 'REPLAN_REQUIRED' ||
        state.stage === 'FAILED' ||
        state.stage === 'UNAVAILABLE',
    ).length,
)

const unavailableTaskCount = computed(
  () =>
    Object.values(taskOperations.value).filter((state) => state.stage === 'UNAVAILABLE').length,
)

const planningServiceState = computed(() => {
  if (loading.value) return { label: '正在同步规划状态', healthy: false }
  if (loadError.value) return { label: '任务服务连接失败', healthy: false }
  if (unavailableTaskCount.value > 0) {
    return { label: `${unavailableTaskCount.value} 个任务状态待刷新`, healthy: false }
  }
  return { label: '规划服务已连接', healthy: true }
})

const filteredTasks = computed(() => {
  const normalizedKeyword = keyword.value.trim().toLocaleLowerCase()
  return tasks.value.filter((task) => {
    const stage = operationFor(task.taskNumber).stage
    const matchesStage =
      stageFilter.value === 'ALL' ||
      stage === stageFilter.value ||
      (stageFilter.value === 'ACTION_REQUIRED' &&
        [
          'AWAITING_INPUT',
          'READY_FOR_REVIEW',
          'AWAITING_RELEASE',
          'REPLAN_REQUIRED',
          'FAILED',
          'UNAVAILABLE',
        ].includes(stage)) ||
      (stageFilter.value === 'EXCEPTION' &&
        ['REPLAN_REQUIRED', 'FAILED', 'UNAVAILABLE'].includes(stage))
    const matchesKeyword =
      !normalizedKeyword ||
      task.taskName.toLocaleLowerCase().includes(normalizedKeyword) ||
      task.taskNumber.toLocaleLowerCase().includes(normalizedKeyword)
    return matchesStage && matchesKeyword
  })
})

function createSourceDraft(sourceType: LoadPlanningSourceType): SourceDraft {
  return {
    sourceType,
    sourceReference: '',
    sourceLocator: '',
    confidence: null,
    verified: false,
  }
}

function createCargoDraft(): CargoDraft {
  return {
    itemCode: '',
    skuCode: '',
    cargoName: '',
    quantity: null,
    lengthMm: null,
    widthMm: null,
    heightMm: null,
    grossWeightKg: null,
    maxStackLayers: null,
    maxTopLoadKg: null,
    stackable: null,
    keepUpright: null,
    fragile: null,
    ...createSourceDraft('SYNTHETIC_DEMO'),
  }
}

function createPalletDraft(): PalletDraft {
  return {
    palletCode: '',
    palletName: '',
    quantity: null,
    lengthMm: null,
    widthMm: null,
    heightMm: null,
    tareWeightKg: null,
    maxLoadKg: null,
    maxTotalHeightMm: null,
    ...createSourceDraft('SYNTHETIC_DEMO'),
  }
}

function createTaskDraft(): TaskDraft {
  return {
    taskNumber: '',
    taskName: '',
    description: '',
    cargoItems: [createCargoDraft()],
    palletSpecs: [createPalletDraft()],
  }
}

const form = reactive<TaskDraft>(createTaskDraft())

const detailBoxCount = computed(
  () => detail.value?.cargoItems.reduce((total, item) => total + item.quantity, 0) ?? 0,
)

function operationFor(taskNumber: string): TaskOperationalState {
  return (
    taskOperations.value[taskNumber] ?? {
      stage: 'PENDING',
      activeRunStatus: null,
      latestRunStatus: null,
      currentStep: null,
      runVersion: null,
      pendingQuestionCount: 0,
      candidatePlanCount: 0,
      selectedPlanCode: null,
      failureMessage: null,
      formalVersionNumber: null,
      formalPlanCode: null,
      formalLockedAt: null,
    }
  )
}

function stageLabel(stage: TaskStageCode): string {
  const labels: Record<TaskStageCode, string> = {
    PENDING: '待规划',
    AWAITING_INPUT: '待补充约束',
    READY_FOR_REVIEW: '待方案审批',
    AWAITING_RELEASE: '待正式发布',
    REPLAN_REQUIRED: '待重新规划',
    FAILED: '规划异常',
    LOCKED: '正式版本已锁定',
    UNAVAILABLE: '状态待刷新',
  }
  return labels[stage]
}

function taskActionLabel(stage: TaskStageCode): string {
  const labels: Record<TaskStageCode, string> = {
    PENDING: '开始规划',
    AWAITING_INPUT: '补充约束',
    READY_FOR_REVIEW: '审批方案',
    AWAITING_RELEASE: '正式发布',
    REPLAN_REQUIRED: '重新规划',
    FAILED: '处理异常',
    LOCKED: '查看方案',
    UNAVAILABLE: '打开工作台',
  }
  return labels[stage]
}

function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    DRAFT: '草稿',
    ACTIVE: '进行中',
    COMPLETED: '已完成',
    CANCELLED: '已取消',
  }
  return labels[status] ?? status
}

function operationDetail(state: TaskOperationalState): string {
  if (state.stage === 'AWAITING_INPUT') {
    return `${state.pendingQuestionCount} 项关键约束等待确认`
  }
  if (state.stage === 'READY_FOR_REVIEW') {
    return `${state.candidatePlanCount} 套候选方案等待审批`
  }
  if (state.stage === 'AWAITING_RELEASE') {
    return state.selectedPlanCode
      ? `${state.selectedPlanCode} 已批准，等待正式发布`
      : '候选方案已批准，等待正式发布'
  }
  if (state.stage === 'REPLAN_REQUIRED') {
    return '上一轮方案已驳回，需要发起独立重规划'
  }
  if (state.stage === 'FAILED') {
    return state.failureMessage || '规划运行失败，需要检查后重试'
  }
  if (state.stage === 'LOCKED') {
    return state.formalPlanCode ? `${state.formalPlanCode} 已锁定` : '正式方案已锁定'
  }
  if (state.stage === 'UNAVAILABLE') {
    return '运行或正式版本状态暂未同步'
  }
  return '尚未启动装载规划'
}

function runReference(state: TaskOperationalState): string {
  const stepLabels: Record<LoadPlanningAgentCurrentStep, string> = {
    CHECKING_CONSTRAINTS: '校验约束',
    AWAITING_CONSTRAINTS: '等待补充',
    GENERATING_PLANS: '生成方案',
    AWAITING_REVIEW: '等待审批',
    FAILED: '执行失败',
    COMPLETED: '运行完成',
  }
  if (state.runVersion !== null) {
    const step = state.currentStep ? ` · ${stepLabels[state.currentStep]}` : ''
    return `运行版本 R${state.runVersion}${step}`
  }
  return '暂无运行'
}

function formatRefreshTime(value: Date | null): string {
  if (!value) return '尚未同步'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(value)
}

function selectStageFilter(filter: TaskFilterCode): void {
  stageFilter.value = filter
}

/**
 * 列表摘要只包含任务主数据；企业工作台还需要组合“可继续运行、完整运行历史、正式版本”
 * 三条事实，才能区分待补约束、待审批、已批准待发布、失败待处理和已经正式锁定。
 *
 * 每个任务的状态读取相互隔离，某一条暂时失败不会让整张任务表不可用；它只显示
 * “状态待刷新”，避免前端把未知状态误报成“待规划”。
 */
async function loadTaskOperations(taskSummaries: LoadPlanningTaskSummary[]): Promise<void> {
  const entries = await Promise.all(
    taskSummaries.map(async (task) => {
      try {
        const [activeRuns, runHistory, versions] = await Promise.all([
          getActiveLoadPlanningAgentRuns(task.taskNumber),
          getLoadPlanningAgentRunHistory(task.taskNumber),
          getLoadPlanVersions(task.taskNumber),
        ])
        const activeRun = activeRuns[0]
        const latestRun = runHistory[0]
        const latestVersion = [...versions].sort(
          (left, right) => right.versionNumber - left.versionNumber,
        )[0]
        const effectiveRun = activeRun ?? latestRun
        let stage: TaskStageCode = 'PENDING'
        if (activeRun?.status === 'AWAITING_INPUT') stage = 'AWAITING_INPUT'
        else if (activeRun?.status === 'READY_FOR_REVIEW') stage = 'READY_FOR_REVIEW'
        else if (
          latestRun?.status === 'APPROVED' &&
          !versions.some((version) => version.sourceRunId === latestRun.runId)
        ) {
          stage = 'AWAITING_RELEASE'
        } else if (latestRun?.status === 'REJECTED') stage = 'REPLAN_REQUIRED'
        else if (latestRun?.status === 'FAILED') stage = 'FAILED'
        else if (latestVersion) stage = 'LOCKED'

        return [
          task.taskNumber,
          {
            stage,
            activeRunStatus: activeRun?.status ?? null,
            latestRunStatus: latestRun?.status ?? null,
            currentStep: effectiveRun?.currentStep ?? null,
            runVersion: effectiveRun?.runVersion ?? null,
            pendingQuestionCount: activeRun?.questions.length ?? 0,
            candidatePlanCount: activeRun?.plans.length ?? latestRun?.plans.length ?? 0,
            selectedPlanCode: latestRun?.selectedPlanCode ?? activeRun?.selectedPlanCode ?? null,
            failureMessage:
              latestRun?.status === 'FAILED' ? latestRun.failureMessage : null,
            formalVersionNumber: latestVersion?.versionNumber ?? null,
            formalPlanCode: latestVersion?.planCode ?? null,
            formalLockedAt: latestVersion?.lockedAt ?? null,
          } satisfies TaskOperationalState,
        ] as const
      } catch {
        return [
          task.taskNumber,
          {
            stage: 'UNAVAILABLE',
            activeRunStatus: null,
            latestRunStatus: null,
            currentStep: null,
            runVersion: null,
            pendingQuestionCount: 0,
            candidatePlanCount: 0,
            selectedPlanCode: null,
            failureMessage: null,
            formalVersionNumber: null,
            formalPlanCode: null,
            formalLockedAt: null,
          } satisfies TaskOperationalState,
        ] as const
      }
    }),
  )
  taskOperations.value = Object.fromEntries(entries)
}

async function loadTasks(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const loadedTasks = await getLoadPlanningTasks()
    tasks.value = loadedTasks
    await loadTaskOperations(loadedTasks)
    lastRefreshedAt.value = new Date()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '读取装载任务失败'
  } finally {
    loading.value = false
  }
}

function openCreateDrawer(): void {
  Object.assign(form, createTaskDraft())
  formError.value = ''
  successMessage.value = ''
  showCreateDrawer.value = true
}

/**
 * 用历史模式中真正明确的托盘外廓和皮重生成候选规格。
 *
 * 相同长宽和皮重只生成一行。多个模式行的 palletQuantity 语义可能重叠，因此这里不求和：
 * - 整份文件只有一种托盘规格时，只使用文档声明的总托数；
 * - 存在多种规格时，只有单独一个模式行的规格才使用该行明确数量；
 * - 其余情况留空，交给业务人员补充。
 */
function buildImportedPallets(
  patterns: HistoricalPalletPattern[],
  declaredTotalPallets: number | null,
  filename: string,
): PalletDraft[] {
  const groups = new Map<string, HistoricalPalletPattern[]>()
  for (const pattern of patterns) {
    const key = [
      pattern.loadedLengthMm,
      pattern.loadedWidthMm,
      pattern.palletTareWeightKg,
    ].join('|')
    const current = groups.get(key) ?? []
    current.push(pattern)
    groups.set(key, current)
  }

  return [...groups.values()].map((group, index) => {
    const representative = group[0]!
    const quantity =
      groups.size === 1
        ? declaredTotalPallets
        : group.length === 1
          ? representative.palletQuantity
          : null

    return {
      ...createPalletDraft(),
      palletCode: `PL-PALLET-${index + 1}`,
      palletName: `${representative.loadedLengthMm}×${representative.loadedWidthMm} 历史托盘候选`,
      quantity,
      lengthMm: representative.loadedLengthMm,
      widthMm: representative.loadedWidthMm,
      // loadedHeightMm 是含货后的历史高度，绝不能当成空托高度或安全上限。
      heightMm: null,
      tareWeightKg: representative.palletTareWeightKg,
      maxLoadKg: null,
      maxTotalHeightMm: null,
      sourceType: 'DOCUMENT_IMPORT',
      sourceReference: filename,
      sourceLocator: representative.sourceLocator,
      confidence: null,
      verified: false,
    }
  })
}

/**
 * PL 预览只负责填充已有证据，不会提交创建请求，也不会猜测缺失的安全参数。
 */
function applyPackingListPreview(payload: {
  filename: string
  preview: PackingListPreview
}): void {
  const { filename, preview } = payload
  Object.assign(form, createTaskDraft())
  form.description = `由 ${filename} 的 ${preview.parserVersion} 预览带入，所有字段仍待人工复核。`
  form.cargoItems = preview.cargoCandidates.map((candidate) => ({
    ...createCargoDraft(),
    itemCode: candidate.itemCode,
    // 创建接口有明确长度上限；预览可以展示完整证据，但带入表单必须先满足主数据边界。
    skuCode: candidate.itemCodes.join('、').slice(0, 128),
    cargoName: candidate.description.slice(0, 255),
    quantity: candidate.cartonQuantity,
    lengthMm: candidate.lengthMm,
    widthMm: candidate.widthMm,
    heightMm: candidate.heightMm,
    grossWeightKg: candidate.cartonGrossWeightKg,
    maxStackLayers: null,
    maxTopLoadKg: null,
    stackable: null,
    keepUpright: null,
    fragile: null,
    sourceType: 'DOCUMENT_IMPORT',
    sourceReference: filename,
    sourceLocator: candidate.sourceLocator,
    confidence: candidate.confidence,
    verified: false,
  }))

  const importedPallets = buildImportedPallets(
    preview.historicalPalletPatterns,
    preview.declaredTotalPallets,
    filename,
  )
  form.palletSpecs = importedPallets.length
    ? importedPallets
    : [{ ...createPalletDraft(), sourceType: 'MANUAL' }]
  formError.value = ''
  successMessage.value = ''
  showPackingListPreview.value = false
  showCreateDrawer.value = true
}

function addCargoItem(): void {
  form.cargoItems.push(createCargoDraft())
}

function removeCargoItem(index: number): void {
  if (form.cargoItems.length > 1) {
    form.cargoItems.splice(index, 1)
  }
}

function addPalletSpec(): void {
  form.palletSpecs.push(createPalletDraft())
}

function removePalletSpec(index: number): void {
  if (form.palletSpecs.length > 1) {
    form.palletSpecs.splice(index, 1)
  }
}

function isPositive(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

/** number 输入被清空时，Vue 运行期可能给出空字符串；发请求前统一转成 null。 */
function normalizeOptionalNumber(value: unknown): number | null {
  if (value === '' || value === null || value === undefined) {
    return null
  }
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

function handleSourceTypeChange(source: SourceDraft): void {
  if (source.sourceType === 'SYNTHETIC_DEMO') {
    source.verified = false
  }
}

function requiresFileTrace(sourceType: LoadPlanningSourceType): boolean {
  return sourceType === 'ENTERPRISE_HISTORICAL' || sourceType === 'DOCUMENT_IMPORT'
}

function validateSource(source: SourceDraft, label: string): string {
  if (
    requiresFileTrace(source.sourceType) &&
    (!source.sourceReference.trim() || !source.sourceLocator.trim())
  ) {
    return `${label}选择文件来源时，必须填写来源名称和页码、Sheet/行号等定位信息。`
  }
  const confidence = normalizeOptionalNumber(source.confidence)
  if (source.confidence !== null && source.confidence !== '' && confidence === null) {
    return `${label}的置信度格式不正确，未知时留空。`
  }
  if (confidence !== null && (confidence < 0 || confidence > 1)) {
    return `${label}的置信度必须在 0 到 1 之间，未知时留空。`
  }
  return ''
}

/** 页面先给出可定位到具体行的错误；Python 和数据库仍会做最终校验。 */
function validateForm(): string {
  if (!/^LP-[A-Za-z0-9_-]+$/.test(form.taskNumber.trim())) {
    return '任务编号必须以 LP- 开头，并且只能包含字母、数字、下划线和短横线。'
  }
  if (!form.taskName.trim()) {
    return '请输入任务名称。'
  }

  const itemCodes = new Set<string>()
  for (const [index, item] of form.cargoItems.entries()) {
    const row = `货物 ${index + 1}`
    if (!item.itemCode.trim() || !item.cargoName.trim()) {
      return `${row}必须填写货物编码和名称。`
    }
    if (itemCodes.has(item.itemCode.trim())) {
      return `货物编码 ${item.itemCode.trim()} 重复。`
    }
    itemCodes.add(item.itemCode.trim())
    if (
      !isPositive(item.quantity) ||
      !isPositive(item.lengthMm) ||
      !isPositive(item.widthMm) ||
      !isPositive(item.heightMm) ||
      !isPositive(item.grossWeightKg)
    ) {
      return `${row}的箱数、长宽高和单箱毛重都必须大于 0。`
    }
    const maxStackLayers = normalizeOptionalNumber(item.maxStackLayers)
    const maxTopLoadKg = normalizeOptionalNumber(item.maxTopLoadKg)
    if (
      (maxStackLayers !== null && !isPositive(maxStackLayers)) ||
      (maxTopLoadKg !== null && !isPositive(maxTopLoadKg))
    ) {
      return `${row}的可选安全参数填写后必须大于 0，未知时请留空。`
    }
    const sourceError = validateSource(item, row)
    if (sourceError) return sourceError
  }

  const palletCodes = new Set<string>()
  for (const [index, pallet] of form.palletSpecs.entries()) {
    const row = `托盘 ${index + 1}`
    if (!pallet.palletCode.trim() || !pallet.palletName.trim()) {
      return `${row}必须填写托盘编码和名称。`
    }
    if (palletCodes.has(pallet.palletCode.trim())) {
      return `托盘编码 ${pallet.palletCode.trim()} 重复。`
    }
    palletCodes.add(pallet.palletCode.trim())
    if (
      !isPositive(pallet.quantity) ||
      !isPositive(pallet.lengthMm) ||
      !isPositive(pallet.widthMm) ||
      !isPositive(pallet.heightMm) ||
      !isPositive(pallet.tareWeightKg)
    ) {
      return `${row}的数量、长宽高和皮重都必须大于 0。`
    }
    const maxLoadKg = normalizeOptionalNumber(pallet.maxLoadKg)
    const maxTotalHeightMm = normalizeOptionalNumber(pallet.maxTotalHeightMm)
    if (
      (maxLoadKg !== null && !isPositive(maxLoadKg)) ||
      (maxTotalHeightMm !== null && !isPositive(maxTotalHeightMm))
    ) {
      return `${row}的最大载重和最大成托高度填写后必须大于 0，未知时请留空。`
    }
    const sourceError = validateSource(pallet, row)
    if (sourceError) return sourceError
  }
  return ''
}

function nullableText(value: string): string | null {
  return value.trim() || null
}

function toCreateInput(): CreateLoadPlanningTaskInput {
  return {
    taskNumber: form.taskNumber.trim(),
    taskName: form.taskName.trim(),
    description: nullableText(form.description),
    cargoItems: form.cargoItems.map((item) => ({
      itemCode: item.itemCode.trim(),
      skuCode: nullableText(item.skuCode),
      cargoName: item.cargoName.trim(),
      quantity: Number(item.quantity),
      lengthMm: Number(item.lengthMm),
      widthMm: Number(item.widthMm),
      heightMm: Number(item.heightMm),
      grossWeightKg: Number(item.grossWeightKg),
      maxStackLayers: normalizeOptionalNumber(item.maxStackLayers),
      maxTopLoadKg: normalizeOptionalNumber(item.maxTopLoadKg),
      stackable: item.stackable,
      keepUpright: item.keepUpright,
      fragile: item.fragile,
      sourceType: item.sourceType,
      sourceReference: nullableText(item.sourceReference),
      sourceLocator: nullableText(item.sourceLocator),
      confidence: normalizeOptionalNumber(item.confidence),
      verified: item.verified,
    })),
    palletSpecs: form.palletSpecs.map((pallet) => ({
      palletCode: pallet.palletCode.trim(),
      palletName: pallet.palletName.trim(),
      quantity: Number(pallet.quantity),
      lengthMm: Number(pallet.lengthMm),
      widthMm: Number(pallet.widthMm),
      heightMm: Number(pallet.heightMm),
      tareWeightKg: Number(pallet.tareWeightKg),
      maxLoadKg: normalizeOptionalNumber(pallet.maxLoadKg),
      maxTotalHeightMm: normalizeOptionalNumber(pallet.maxTotalHeightMm),
      sourceType: pallet.sourceType,
      sourceReference: nullableText(pallet.sourceReference),
      sourceLocator: nullableText(pallet.sourceLocator),
      confidence: normalizeOptionalNumber(pallet.confidence),
      verified: pallet.verified,
    })),
  }
}

async function submitTask(): Promise<void> {
  formError.value = validateForm()
  if (formError.value) return

  submitting.value = true
  try {
    const created = await createLoadPlanningTask(toCreateInput())
    showCreateDrawer.value = false
    successMessage.value = `装载任务“${created.taskName}”已创建。`
    await loadTasks()
    await openTaskDetail(created.taskNumber)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '创建装载任务失败'
  } finally {
    submitting.value = false
  }
}

async function openTaskDetail(taskNumber: string): Promise<void> {
  detail.value = null
  detailError.value = ''
  detailLoading.value = true
  showDetailDrawer.value = true
  try {
    detail.value = await getLoadPlanningTask(taskNumber)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '读取装载任务详情失败'
  } finally {
    detailLoading.value = false
  }
}

async function openTaskPlanning(taskNumber: string): Promise<void> {
  planningTaskNumber.value = taskNumber
  loadError.value = ''
  try {
    agentTask.value = await getLoadPlanningTask(taskNumber)
    showAgentWorkbench.value = true
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '打开装载规划工作台失败'
  } finally {
    planningTaskNumber.value = ''
  }
}

/**
 * 智能体工作台使用刚从 Python 权威接口读取的完整任务，不使用列表摘要。
 *
 * 工作台打开后会创建一次有状态运行；如果发现安全约束缺失，后端会返回结构化问题并暂停，
 * 而不是由浏览器或大模型猜测。
 */
function openAgentWorkbench(): void {
  if (!detail.value) return
  agentTask.value = detail.value
  showDetailDrawer.value = false
  showAgentWorkbench.value = true
}

function formatDate(value: string | null): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function sourceLabel(sourceType: LoadPlanningSourceType): string {
  return sourceOptions.find((option) => option.value === sourceType)?.label ?? sourceType
}

function optionalValue(value: number | boolean | null, unit = ''): string {
  if (value === null) return '未知'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return `${value}${unit}`
}

function routeQueryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] ?? '').trim()
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * 驾驶舱和案件工作台可以携带稳定任务编号直接进入具体对象。
 *
 * URL 只负责定位；真正的完整任务仍由 Java BFF 重新读取，避免把列表摘要当成权威输入。
 */
async function openRouteRequestedTask(): Promise<void> {
  const taskNumber = routeQueryValue(route.query.taskNumber)
  if (!taskNumber) return

  if (routeQueryValue(route.query.view) === 'detail') {
    await openTaskDetail(taskNumber)
  } else {
    await openTaskPlanning(taskNumber)
  }
}

onMounted(async () => {
  await loadTasks()
  await openRouteRequestedTask()
})
</script>

<template>
  <section class="load-planning-page load-operations-page">
    <header class="load-task-toolbar">
      <div class="load-task-toolbar-copy">
        <div class="load-task-title-row">
          <strong>任务运行概况</strong>
          <span
            class="load-task-service-status"
            :class="{ healthy: planningServiceState.healthy }"
          >
            <i></i>
            {{ planningServiceState.label }}
          </span>
        </div>
        <div class="load-task-toolbar-meta">
          <span><UiIcon name="clock" />最近同步 {{ formatRefreshTime(lastRefreshedAt) }}</span>
          <span><UiIcon name="box" />{{ tasks.length }} 个任务 · {{ totalBoxes }} 箱</span>
          <span><UiIcon name="alert" />{{ actionRequiredTaskCount }} 项需要处理</span>
          <span class="load-task-unit">统一单位 mm / kg</span>
        </div>
      </div>
      <div class="load-task-toolbar-actions">
        <button class="secondary-button" type="button" :disabled="loading" @click="loadTasks">
          <UiIcon name="refresh" />
          {{ loading ? '同步中' : '刷新' }}
        </button>
        <button class="secondary-button" type="button" @click="showPackingListPreview = true">
          <UiIcon name="upload" />
          从 PL 识别
        </button>
        <button class="primary-button" type="button" @click="openCreateDrawer">
          <UiIcon name="plus" />
          新建装载任务
        </button>
      </div>
    </header>

    <div v-if="successMessage" class="feedback-banner success" role="status">
      <span>✓</span>
      <p>{{ successMessage }}</p>
    </div>
    <div v-if="loadError" class="feedback-banner error" role="alert">
      <span>!</span>
      <p>{{ loadError }}</p>
      <button type="button" @click="loadTasks">重新加载</button>
    </div>

    <section class="load-task-stage-summary" aria-label="按业务阶段查看任务">
      <button
        type="button"
        :class="{ active: stageFilter === 'ALL' }"
        :aria-pressed="stageFilter === 'ALL'"
        @click="selectStageFilter('ALL')"
      >
        <span class="load-task-summary-icon neutral"><UiIcon name="layers" /></span>
        <span><small>全部任务</small><strong>{{ tasks.length }}</strong></span>
      </button>
      <button
        type="button"
        :class="{ active: stageFilter === 'AWAITING_INPUT' }"
        :aria-pressed="stageFilter === 'AWAITING_INPUT'"
        @click="selectStageFilter('AWAITING_INPUT')"
      >
        <span class="load-task-summary-icon warning"><UiIcon name="file" /></span>
        <span><small>待补约束</small><strong>{{ awaitingInputTaskCount }}</strong></span>
      </button>
      <button
        type="button"
        :class="{ active: stageFilter === 'READY_FOR_REVIEW' }"
        :aria-pressed="stageFilter === 'READY_FOR_REVIEW'"
        @click="selectStageFilter('READY_FOR_REVIEW')"
      >
        <span class="load-task-summary-icon review"><UiIcon name="check" /></span>
        <span><small>待方案审批</small><strong>{{ awaitingReviewTaskCount }}</strong></span>
      </button>
      <button
        type="button"
        :class="{ active: stageFilter === 'AWAITING_RELEASE' }"
        :aria-pressed="stageFilter === 'AWAITING_RELEASE'"
        @click="selectStageFilter('AWAITING_RELEASE')"
      >
        <span class="load-task-summary-icon release"><UiIcon name="upload" /></span>
        <span><small>待正式发布</small><strong>{{ awaitingReleaseTaskCount }}</strong></span>
      </button>
      <button
        type="button"
        :class="{ active: stageFilter === 'EXCEPTION' }"
        :aria-pressed="stageFilter === 'EXCEPTION'"
        @click="selectStageFilter('EXCEPTION')"
      >
        <span class="load-task-summary-icon danger"><UiIcon name="alert" /></span>
        <span><small>异常 / 重规划</small><strong>{{ exceptionTaskCount }}</strong></span>
      </button>
      <button
        type="button"
        :class="{ active: stageFilter === 'LOCKED' }"
        :aria-pressed="stageFilter === 'LOCKED'"
        @click="selectStageFilter('LOCKED')"
      >
        <span class="load-task-summary-icon success"><UiIcon name="shield" /></span>
        <span><small>正式版本</small><strong>{{ lockedTaskCount }}</strong></span>
      </button>
    </section>

    <div class="panel load-task-panel">
      <div class="management-toolbar">
        <div>
          <h3>任务队列</h3>
          <p>处理约束补录、方案审批、正式发布和异常重规划</p>
        </div>
        <span class="load-task-result-count">
          显示 {{ filteredTasks.length }} / {{ tasks.length }}
        </span>
      </div>

      <div class="load-task-filterbar">
        <label class="load-task-search">
          <UiIcon name="search" />
          <input v-model="keyword" type="search" placeholder="搜索任务名称或任务编号" />
        </label>
        <label class="load-task-stage-filter">
          <span>业务阶段</span>
          <select v-model="stageFilter">
            <option value="ALL">全部阶段</option>
            <option value="ACTION_REQUIRED">全部待处理</option>
            <option value="PENDING">待规划</option>
            <option value="AWAITING_INPUT">待补充约束</option>
            <option value="READY_FOR_REVIEW">待方案审批</option>
            <option value="AWAITING_RELEASE">待正式发布</option>
            <option value="EXCEPTION">异常与重规划</option>
            <option value="REPLAN_REQUIRED">待重新规划</option>
            <option value="FAILED">规划异常</option>
            <option value="LOCKED">正式版本已锁定</option>
            <option value="UNAVAILABLE">状态待刷新</option>
          </select>
        </label>
        <button
          v-if="keyword || stageFilter !== 'ALL'"
          class="load-task-clear-filter"
          type="button"
          @click="keyword = ''; stageFilter = 'ALL'"
        >
          清除筛选
        </button>
      </div>

      <div v-if="loading" class="empty-state">
        <span class="loading-ring"></span>
        <strong>正在汇总任务与规划状态</strong>
        <p>正在读取任务、待办运行和正式版本信息。</p>
      </div>

      <div v-else-if="tasks.length === 0" class="load-task-empty">
        <span class="empty-task-icon"><UiIcon name="box" /></span>
        <div>
          <strong>当前没有装载任务</strong>
          <p>可从 Packing List 导入候选数据，或直接录入已确认的箱规和托盘资源。</p>
        </div>
        <button class="primary-button" type="button" @click="openCreateDrawer">
          新建装载任务
        </button>
      </div>

      <div v-else-if="filteredTasks.length === 0" class="load-task-empty compact">
        <span class="empty-task-icon"><UiIcon name="search" /></span>
        <div>
          <strong>没有匹配的任务</strong>
          <p>请调整关键词或业务阶段筛选条件。</p>
        </div>
        <button
          class="secondary-button"
          type="button"
          @click="keyword = ''; stageFilter = 'ALL'"
        >
          清除筛选
        </button>
      </div>

      <div v-else class="table-wrap">
        <table class="data-table load-task-table">
          <thead>
            <tr>
              <th>任务</th>
              <th>任务规模</th>
              <th>规划进度</th>
              <th>正式版本</th>
              <th>最近更新</th>
              <th>下一步</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in filteredTasks" :key="task.id">
              <td>
                <div class="load-task-identity">
                  <div>
                    <strong>{{ task.taskName }}</strong>
                    <span>{{ taskStatusLabel(task.status) }}</span>
                  </div>
                  <small>{{ task.taskNumber }}</small>
                </div>
              </td>
              <td>
                <div class="load-task-workload">
                  <strong>{{ task.cargoBoxCount.toLocaleString('zh-CN') }} 箱</strong>
                  <small>
                    {{ task.cargoItemCount }} 个货物项 ·
                    {{ task.palletSpecCount }} 个托盘规格
                  </small>
                </div>
              </td>
              <td>
                <div class="load-task-progress">
                  <span
                    class="task-stage-chip"
                    :data-stage="operationFor(task.taskNumber).stage"
                  >
                    <i></i>
                    {{ stageLabel(operationFor(task.taskNumber).stage) }}
                  </span>
                  <p>{{ operationDetail(operationFor(task.taskNumber)) }}</p>
                  <small>{{ runReference(operationFor(task.taskNumber)) }}</small>
                </div>
              </td>
              <td>
                <div
                  v-if="operationFor(task.taskNumber).formalVersionNumber"
                  class="load-task-version locked"
                >
                  <div>
                    <strong>V{{ operationFor(task.taskNumber).formalVersionNumber }}</strong>
                    <span>已锁定</span>
                  </div>
                  <small>{{ operationFor(task.taskNumber).formalPlanCode }}</small>
                  <em>
                    {{
                      operationFor(task.taskNumber).formalLockedAt
                        ? formatDate(operationFor(task.taskNumber).formalLockedAt)
                        : '正式版本'
                    }}
                  </em>
                </div>
                <div v-else class="load-task-version">
                  <strong>未发布</strong>
                  <small>暂无正式执行版本</small>
                </div>
              </td>
              <td class="load-task-updated">
                <strong>{{ formatDate(task.updatedAt) }}</strong>
                <small>任务主数据</small>
              </td>
              <td class="load-task-actions">
                <button
                  class="table-action-button"
                  type="button"
                  @click="openTaskDetail(task.taskNumber)"
                >
                  详情
                </button>
                <button
                  class="table-action-button primary"
                  type="button"
                  :disabled="planningTaskNumber === task.taskNumber"
                  @click="openTaskPlanning(task.taskNumber)"
                >
                  {{
                    planningTaskNumber === task.taskNumber
                      ? '正在打开…'
                      : taskActionLabel(operationFor(task.taskNumber).stage)
                  }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <PackingListPreviewDrawer
      v-model="showPackingListPreview"
      @apply="applyPackingListPreview"
    />

    <div v-if="showCreateDrawer" class="drawer-backdrop" @click.self="showCreateDrawer = false">
      <aside class="form-drawer load-task-drawer" aria-labelledby="create-load-task-title">
        <div class="drawer-header">
          <div>
            <h3 id="create-load-task-title">创建装载任务</h3>
            <p>保存可追溯的任务输入，创建后即可进入智能规划与方案审批。</p>
          </div>
          <button class="close-button" type="button" @click="showCreateDrawer = false">
            <UiIcon name="close" />
          </button>
        </div>

        <form @submit.prevent="submitTask">
          <section class="load-form-section">
            <div class="load-form-heading">
              <div><span>01</span><h4>任务信息</h4></div>
              <small>任务编号创建后作为稳定查询标识</small>
            </div>
            <div class="load-form-grid">
              <label class="form-field">
                <span>任务编号 <em>必填</em></span>
                <input v-model="form.taskNumber" maxlength="64" placeholder="LP-20261012-001" />
              </label>
              <label class="form-field">
                <span>任务名称 <em>必填</em></span>
                <input v-model="form.taskName" maxlength="128" placeholder="例如：华东仓出货装托任务" />
              </label>
            </div>
            <label class="form-field">
              <span>任务说明</span>
              <textarea
                v-model="form.description"
                maxlength="2000"
                placeholder="说明任务目标、业务背景或需要比较的历史方案"
              ></textarea>
            </label>
          </section>

          <section class="load-form-section">
            <div class="load-form-heading">
              <div><span>02</span><h4>货物箱规</h4></div>
              <button class="secondary-button" type="button" @click="addCargoItem">
                <UiIcon name="plus" />
                添加货物
              </button>
            </div>

            <article
              v-for="(item, index) in form.cargoItems"
              :key="index"
              class="load-input-card"
            >
              <div class="load-input-card-header">
                <strong>货物 {{ index + 1 }}</strong>
                <button
                  type="button"
                  :disabled="form.cargoItems.length === 1"
                  @click="removeCargoItem(index)"
                >
                  删除
                </button>
              </div>
              <div class="load-form-grid three">
                <label class="form-field">
                  <span>货物编码 <em>必填</em></span>
                  <input v-model="item.itemCode" maxlength="64" placeholder="ITEM-001" />
                </label>
                <label class="form-field">
                  <span>SKU</span>
                  <input v-model="item.skuCode" maxlength="128" placeholder="可选" />
                </label>
                <label class="form-field">
                  <span>货物名称 <em>必填</em></span>
                  <input v-model="item.cargoName" maxlength="255" placeholder="产品 A 外箱" />
                </label>
              </div>
              <div class="load-form-grid five">
                <label class="form-field">
                  <span>箱数 <em>必填</em></span>
                  <input v-model.number="item.quantity" type="number" min="1" step="1" />
                </label>
                <label class="form-field">
                  <span>长 mm <em>必填</em></span>
                  <input v-model.number="item.lengthMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>宽 mm <em>必填</em></span>
                  <input v-model.number="item.widthMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>高 mm <em>必填</em></span>
                  <input v-model.number="item.heightMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>单箱毛重 kg <em>必填</em></span>
                  <input
                    v-model.number="item.grossWeightKg"
                    type="number"
                    min="0.001"
                    step="0.001"
                  />
                </label>
              </div>

              <details class="optional-fields">
                <summary>安全参数与来源追踪（未知安全参数请留空）</summary>
                <div class="load-form-grid three">
                  <label class="form-field">
                    <span>最大堆码层数</span>
                    <input v-model.number="item.maxStackLayers" type="number" min="1" step="1" />
                  </label>
                  <label class="form-field">
                    <span>顶部最大承重 kg</span>
                    <input v-model.number="item.maxTopLoadKg" type="number" min="0.001" step="0.001" />
                  </label>
                  <label class="form-field">
                    <span>允许堆码</span>
                    <select v-model="item.stackable">
                      <option :value="null">未知</option>
                      <option :value="true">是</option>
                      <option :value="false">否</option>
                    </select>
                  </label>
                  <label class="form-field">
                    <span>必须向上</span>
                    <select v-model="item.keepUpright">
                      <option :value="null">未知</option>
                      <option :value="true">是</option>
                      <option :value="false">否</option>
                    </select>
                  </label>
                  <label class="form-field">
                    <span>易碎</span>
                    <select v-model="item.fragile">
                      <option :value="null">未知</option>
                      <option :value="true">是</option>
                      <option :value="false">否</option>
                    </select>
                  </label>
                </div>
                <div class="load-form-grid three">
                  <label class="form-field">
                    <span>来源类型 <em>必填</em></span>
                    <select v-model="item.sourceType" @change="handleSourceTypeChange(item)">
                      <option v-for="option in sourceOptions" :key="option.value" :value="option.value">
                        {{ option.label }}
                      </option>
                    </select>
                  </label>
                  <label class="form-field">
                    <span>来源名称</span>
                    <input v-model="item.sourceReference" placeholder="文件名或确认记录" />
                  </label>
                  <label class="form-field">
                    <span>来源位置</span>
                    <input v-model="item.sourceLocator" placeholder="页码、Sheet/行号或单元格" />
                  </label>
                  <label class="form-field">
                    <span>解析置信度</span>
                    <input v-model.number="item.confidence" type="number" min="0" max="1" step="0.01" />
                  </label>
                  <label class="check-field">
                    <input
                      v-model="item.verified"
                      type="checkbox"
                      :disabled="item.sourceType === 'SYNTHETIC_DEMO'"
                    />
                    <span>
                      {{
                        item.sourceType === 'SYNTHETIC_DEMO'
                          ? '构造数据不能标记为已核对正式数据'
                          : '已由业务人员核对'
                      }}
                    </span>
                  </label>
                </div>
              </details>
            </article>
          </section>

          <section class="load-form-section">
            <div class="load-form-heading">
              <div><span>03</span><h4>候选托盘规格</h4></div>
              <button class="secondary-button" type="button" @click="addPalletSpec">
                <UiIcon name="plus" />
                添加托盘
              </button>
            </div>

            <article
              v-for="(pallet, index) in form.palletSpecs"
              :key="index"
              class="load-input-card"
            >
              <div class="load-input-card-header">
                <strong>托盘 {{ index + 1 }}</strong>
                <button
                  type="button"
                  :disabled="form.palletSpecs.length === 1"
                  @click="removePalletSpec(index)"
                >
                  删除
                </button>
              </div>
              <div class="load-form-grid">
                <label class="form-field">
                  <span>托盘编码 <em>必填</em></span>
                  <input v-model="pallet.palletCode" maxlength="64" placeholder="PALLET-1200X800" />
                </label>
                <label class="form-field">
                  <span>托盘名称 <em>必填</em></span>
                  <input v-model="pallet.palletName" maxlength="128" placeholder="1200×800 熏蒸托盘" />
                </label>
              </div>
              <div class="load-form-grid five">
                <label class="form-field">
                  <span>可用数量 <em>必填</em></span>
                  <input v-model.number="pallet.quantity" type="number" min="1" step="1" />
                </label>
                <label class="form-field">
                  <span>长 mm <em>必填</em></span>
                  <input v-model.number="pallet.lengthMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>宽 mm <em>必填</em></span>
                  <input v-model.number="pallet.widthMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>空托高度 mm <em>必填</em></span>
                  <input v-model.number="pallet.heightMm" type="number" min="0.001" step="0.001" />
                </label>
                <label class="form-field">
                  <span>皮重 kg <em>必填</em></span>
                  <input
                    v-model.number="pallet.tareWeightKg"
                    type="number"
                    min="0.001"
                    step="0.001"
                  />
                </label>
              </div>
              <details class="optional-fields">
                <summary>安全参数与来源追踪（未知安全参数请留空）</summary>
                <div class="load-form-grid">
                  <label class="form-field">
                    <span>最大载重 kg</span>
                    <input v-model.number="pallet.maxLoadKg" type="number" min="0.001" step="0.001" />
                  </label>
                  <label class="form-field">
                    <span>含货最大高度 mm</span>
                    <input
                      v-model.number="pallet.maxTotalHeightMm"
                      type="number"
                      min="0.001"
                      step="0.001"
                    />
                  </label>
                </div>
                <div class="load-form-grid three">
                  <label class="form-field">
                    <span>来源类型 <em>必填</em></span>
                    <select v-model="pallet.sourceType" @change="handleSourceTypeChange(pallet)">
                      <option v-for="option in sourceOptions" :key="option.value" :value="option.value">
                        {{ option.label }}
                      </option>
                    </select>
                  </label>
                  <label class="form-field">
                    <span>来源名称</span>
                    <input v-model="pallet.sourceReference" placeholder="文件名或确认记录" />
                  </label>
                  <label class="form-field">
                    <span>来源位置</span>
                    <input v-model="pallet.sourceLocator" placeholder="页码、Sheet/行号或单元格" />
                  </label>
                  <label class="form-field">
                    <span>解析置信度</span>
                    <input
                      v-model.number="pallet.confidence"
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                    />
                  </label>
                  <label class="check-field">
                    <input
                      v-model="pallet.verified"
                      type="checkbox"
                      :disabled="pallet.sourceType === 'SYNTHETIC_DEMO'"
                    />
                    <span>
                      {{
                        pallet.sourceType === 'SYNTHETIC_DEMO'
                          ? '构造数据不能标记为已核对正式数据'
                          : '已由业务人员核对'
                      }}
                    </span>
                  </label>
                </div>
              </details>
            </article>
            <p
              v-if="form.palletSpecs.some((pallet) => pallet.sourceType === 'DOCUMENT_IMPORT' && !pallet.heightMm)"
              class="imported-field-warning"
            >
              PL 中的 loadedHeightMm 是历史成托高度，不是空托高度。请在每个导入托盘中人工填写
              “空托高度 mm”；最大载重和最大成托高度没有可靠证据时继续留空。
            </p>
          </section>

          <div v-if="formError" class="form-error" role="alert">{{ formError }}</div>
          <div class="drawer-actions load-drawer-actions">
            <button class="secondary-button" type="button" @click="showCreateDrawer = false">
              取消
            </button>
            <button class="primary-button" type="submit" :disabled="submitting">
              {{ submitting ? '正在创建…' : '创建任务' }}
            </button>
          </div>
        </form>
      </aside>
    </div>

    <div v-if="showDetailDrawer" class="drawer-backdrop" @click.self="showDetailDrawer = false">
      <aside class="form-drawer load-detail-drawer" aria-labelledby="load-task-detail-title">
        <div class="drawer-header">
          <div>
            <h3 id="load-task-detail-title">装载任务详情</h3>
            <p>查看任务输入、来源记录、候选托盘资源和后续规划入口。</p>
          </div>
          <button class="close-button" type="button" @click="showDetailDrawer = false">
            <UiIcon name="close" />
          </button>
        </div>

        <div v-if="detailLoading" class="detail-loading">
          <span class="loading-ring"></span>
          正在读取任务详情
        </div>
        <div v-else-if="detailError" class="feedback-banner error">
          <span>!</span><p>{{ detailError }}</p>
        </div>
        <div v-else-if="detail" class="load-detail-content">
          <div class="load-detail-title">
            <div>
              <span>{{ detail.taskNumber }}</span>
              <h4>{{ detail.taskName }}</h4>
              <p>{{ detail.description || '未填写任务说明' }}</p>
            </div>
            <div class="load-detail-primary-actions">
              <span class="status-pill active"><i></i>{{ taskStatusLabel(detail.status) }}</span>
              <button class="primary-button" type="button" @click="openAgentWorkbench">
                <UiIcon name="layers" />
                {{ taskActionLabel(operationFor(detail.taskNumber).stage) }}
              </button>
            </div>
          </div>

          <div class="detail-summary load-detail-summary">
            <div><span>货物品类</span><strong>{{ detail.cargoItems.length }}</strong></div>
            <div><span>总箱数</span><strong>{{ detailBoxCount }}</strong></div>
            <div><span>托盘规格</span><strong>{{ detail.palletSpecs.length }}</strong></div>
            <div>
              <span>固定单位</span>
              <strong>{{ detail.units.dimension }} / {{ detail.units.weight }}</strong>
            </div>
          </div>

          <section class="load-detail-section">
            <div class="load-form-heading">
              <div><span>01</span><h4>货物箱规</h4></div>
            </div>
            <article v-for="item in detail.cargoItems" :key="item.id" class="detail-data-card">
              <div class="detail-data-heading">
                <div><strong>{{ item.cargoName }}</strong><small>{{ item.itemCode }}</small></div>
                <span>{{ item.quantity }} 箱</span>
              </div>
              <dl class="detail-data-grid">
                <div><dt>外箱尺寸</dt><dd>{{ item.lengthMm }} × {{ item.widthMm }} × {{ item.heightMm }} mm</dd></div>
                <div><dt>单箱毛重</dt><dd>{{ item.grossWeightKg }} kg</dd></div>
                <div><dt>最大层数</dt><dd>{{ optionalValue(item.maxStackLayers) }}</dd></div>
                <div><dt>顶部承重</dt><dd>{{ optionalValue(item.maxTopLoadKg, ' kg') }}</dd></div>
                <div><dt>允许堆码</dt><dd>{{ optionalValue(item.stackable) }}</dd></div>
                <div><dt>必须向上</dt><dd>{{ optionalValue(item.keepUpright) }}</dd></div>
                <div><dt>易碎</dt><dd>{{ optionalValue(item.fragile) }}</dd></div>
                <div><dt>数据来源</dt><dd>{{ sourceLabel(item.sourceType) }}</dd></div>
              </dl>
              <p v-if="item.sourceReference" class="source-trace">
                {{ item.sourceReference }} · {{ item.sourceLocator || '未填写位置' }}
                <span>{{ item.verified ? '已核对' : '未核对' }}</span>
              </p>
            </article>
          </section>

          <section class="load-detail-section">
            <div class="load-form-heading">
              <div><span>02</span><h4>候选托盘</h4></div>
            </div>
            <article
              v-for="pallet in detail.palletSpecs"
              :key="pallet.id"
              class="detail-data-card"
            >
              <div class="detail-data-heading">
                <div><strong>{{ pallet.palletName }}</strong><small>{{ pallet.palletCode }}</small></div>
                <span>{{ pallet.quantity }} 个</span>
              </div>
              <dl class="detail-data-grid">
                <div><dt>托盘尺寸</dt><dd>{{ pallet.lengthMm }} × {{ pallet.widthMm }} × {{ pallet.heightMm }} mm</dd></div>
                <div><dt>托盘皮重</dt><dd>{{ pallet.tareWeightKg }} kg</dd></div>
                <div><dt>最大载重</dt><dd>{{ optionalValue(pallet.maxLoadKg, ' kg') }}</dd></div>
                <div><dt>最大成托高度</dt><dd>{{ optionalValue(pallet.maxTotalHeightMm, ' mm') }}</dd></div>
                <div><dt>数据来源</dt><dd>{{ sourceLabel(pallet.sourceType) }}</dd></div>
              </dl>
              <p v-if="pallet.sourceReference" class="source-trace">
                {{ pallet.sourceReference }} · {{ pallet.sourceLocator || '未填写位置' }}
                <span>{{ pallet.verified ? '已核对' : '未核对' }}</span>
              </p>
            </article>
          </section>
        </div>
      </aside>
    </div>

    <AgentPlanningWorkbench
      v-if="agentTask"
      v-model="showAgentWorkbench"
      :task="agentTask"
    />
  </section>
</template>
